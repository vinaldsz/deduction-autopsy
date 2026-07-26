"""SQLite schema + connection helpers for the relational deductions store.

Layer 23 deliverable: the finalized relational schema (see docs/SPEC.md "Relational schema —
FINAL") as idempotent DDL, plus thin connect/init helpers. This is the design gate for the
Layers 24-31 semantic/DB phase — later layers (ETL extract/transform/load, DB-backed tools) build
on the tables defined here.

Scope note: no ETL, no fixtures, no DEDUCTIONS_DB env wiring here (that lands in Layer 27 per
docs/PLAN.md). This module only creates a clean empty DB matching the schema.

Business tables are 1:1 with mcp_server/models.py; the *_uom and claimed_reason CHECK constraints
mirror the models' UOM / ClaimReason Literals. Money and quantities are INTEGER (cents / whole
units); UOM float conversions are computed at query time from data/sku_uom_conversions.json.
"""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "deductions.db"

SCHEMA_SQL = """
-- Operational: daily load batches (dated natural key, e.g. LOT-2026-07-25).
CREATE TABLE IF NOT EXISTS batches (
    batch_id    TEXT PRIMARY KEY,
    load_date   TEXT,
    status      TEXT CHECK (status IN ('complete', 'incomplete', 'complete_with_exceptions')),
    created_at  TEXT
);

-- Business (1:1 with models.PurchaseOrder). Root of the entity graph.
CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id       TEXT PRIMARY KEY,
    retailer    TEXT,
    sku         TEXT,
    ordered_qty INTEGER,
    ordered_uom TEXT CHECK (ordered_uom IN ('EACH', 'CASE', 'PALLET')),
    unit_price  INTEGER,
    order_date  TEXT
);

-- Business (1:1 with models.ASN). 0..N per PO (split shipment = multiple rows).
CREATE TABLE IF NOT EXISTS asns (
    asn_id      TEXT PRIMARY KEY,
    po_id       TEXT,
    sku         TEXT,
    shipped_qty INTEGER,
    shipped_uom TEXT CHECK (shipped_uom IN ('EACH', 'CASE', 'PALLET')),
    ship_date   TEXT,
    carrier     TEXT,
    FOREIGN KEY (po_id) REFERENCES purchase_orders (po_id)
);

-- Business (1:1 with models.Invoice).
CREATE TABLE IF NOT EXISTS invoices (
    invoice_id   TEXT PRIMARY KEY,
    po_id        TEXT,
    sku          TEXT,
    invoiced_qty INTEGER,
    invoiced_uom TEXT CHECK (invoiced_uom IN ('EACH', 'CASE', 'PALLET')),
    invoice_date TEXT,
    amount       INTEGER,
    FOREIGN KEY (po_id) REFERENCES purchase_orders (po_id)
);

-- Business (1:1 with models.ReceivingRecord). Carries free-text notes (injection surface).
CREATE TABLE IF NOT EXISTS receiving_records (
    receipt_id   TEXT PRIMARY KEY,
    po_id        TEXT,
    sku          TEXT,
    received_qty INTEGER,
    received_uom TEXT CHECK (received_uom IN ('EACH', 'CASE', 'PALLET')),
    receipt_date TEXT,
    lot_id       TEXT,
    notes        TEXT,
    FOREIGN KEY (po_id) REFERENCES purchase_orders (po_id)
);

-- Business (1:1 with models.TradeAgreement). Standalone; queried by (retailer, sku, promo_code).
CREATE TABLE IF NOT EXISTS trade_agreements (
    agreement_id   TEXT PRIMARY KEY,
    retailer       TEXT,
    sku            TEXT,
    promo_code     TEXT,
    discount_terms TEXT,
    valid_from     TEXT,
    valid_to       TEXT,
    signed_by      TEXT
);

-- Business (1:1 with models.DeductionClaim + operational batch_id augmentation).
-- Multiple claims per PO allowed (e.g. CLM-007a/007b -> PO-007).
CREATE TABLE IF NOT EXISTS deduction_claims (
    claim_id       TEXT PRIMARY KEY,
    po_id          TEXT,
    batch_id       TEXT,
    retailer       TEXT,
    claimed_reason TEXT CHECK (
        claimed_reason IN ('shortage', 'promo_billback', 'compliance', 'wrong_item')
    ),
    claimed_amount INTEGER,
    claim_date     TEXT,
    retailer_notes TEXT,
    FOREIGN KEY (po_id) REFERENCES purchase_orders (po_id),
    FOREIGN KEY (batch_id) REFERENCES batches (batch_id)
);

-- Operational: one resolution per claim. Seeded for prior claims (CLM-007a/008a) and written by
-- the pipeline in later layers; Layer 23 only defines the table.
CREATE TABLE IF NOT EXISTS claim_resolutions (
    claim_id             TEXT PRIMARY KEY,
    investigator_verdict TEXT,
    final_verdict        TEXT,
    confidence           REAL,
    resolved_at          TEXT,
    run_id               TEXT,
    FOREIGN KEY (claim_id) REFERENCES deduction_claims (claim_id)
);

-- Metadata: quarantine / dead-letter for non-conforming source rows (ETL Transform, Layer 26).
CREATE TABLE IF NOT EXISTS reject_rows (
    id          INTEGER PRIMARY KEY,
    batch_id    TEXT,
    source      TEXT,
    raw_row     TEXT,
    reason      TEXT,
    rejected_at TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches (batch_id)
);

-- Metadata: per-source load counts (ETL Load, Layer 27).
CREATE TABLE IF NOT EXISTS load_audit (
    id            INTEGER PRIMARY KEY,
    batch_id      TEXT,
    source        TEXT,
    rows_read     INTEGER,
    rows_loaded   INTEGER,
    rows_rejected INTEGER,
    loaded_at     TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches (batch_id)
);

-- Metadata: provenance for every loaded business row (ETL Load, Layer 27).
CREATE TABLE IF NOT EXISTS lineage (
    id             INTEGER PRIMARY KEY,
    batch_id       TEXT,
    entity_table   TEXT,
    entity_pk      TEXT,
    source_file    TEXT,
    source_row_ref TEXT,
    loaded_at      TEXT,
    FOREIGN KEY (batch_id) REFERENCES batches (batch_id)
);

-- Per-batch dashboard aggregates (feeds Layer 30 /api/dashboard + run_all summary). Plain view:
-- SQLite has no materialized views and on-read aggregation at this scale is free and always fresh.
CREATE VIEW IF NOT EXISTS v_batch_summary AS
SELECT
    c.batch_id AS batch_id,
    COUNT(*) AS claims_total,
    COUNT(r.claim_id) AS claims_resolved,
    SUM(CASE WHEN r.final_verdict = 'ESCALATE' THEN 1 ELSE 0 END) AS needs_human_review,
    SUM(CASE WHEN r.claim_id IS NULL THEN c.claimed_amount ELSE 0 END) AS dollars_at_risk_cents
FROM deduction_claims c
LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id
GROUP BY c.batch_id;

-- Indexes on FK / lookup columns (PKs are auto-indexed by SQLite, so only non-PK access paths
-- need these). These document the Layer 28 MCP-tool and Layer 30 dashboard access patterns; at
-- this scale they are correctness-neutral but keep the schema DE-grade.
CREATE INDEX IF NOT EXISTS idx_asns_po_id ON asns (po_id);
CREATE INDEX IF NOT EXISTS idx_invoices_po_id ON invoices (po_id);
CREATE INDEX IF NOT EXISTS idx_receiving_records_po_id ON receiving_records (po_id);
CREATE INDEX IF NOT EXISTS idx_deduction_claims_po_id ON deduction_claims (po_id);
CREATE INDEX IF NOT EXISTS idx_deduction_claims_batch_id ON deduction_claims (batch_id);
CREATE INDEX IF NOT EXISTS idx_trade_agreements_lookup
    ON trade_agreements (retailer, sku, promo_code);
CREATE INDEX IF NOT EXISTS idx_reject_rows_batch_id ON reject_rows (batch_id);
CREATE INDEX IF NOT EXISTS idx_load_audit_batch_id ON load_audit (batch_id);
CREATE INDEX IF NOT EXISTS idx_lineage_batch_id ON lineage (batch_id);
-- Reverse-provenance lookup: "where did this exact DB row come from?" (business row -> source).
-- Not UNIQUE: idempotent re-loads / multi-source-row entities can yield >1 lineage row per entity.
CREATE INDEX IF NOT EXISTS idx_lineage_entity ON lineage (entity_table, entity_pk);
"""


def connect(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the DB with foreign-key enforcement on (a per-connection pragma)."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path | str = DEFAULT_DB_PATH) -> None:
    """Create the schema (idempotent) — doubles as the create/migrate helper."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA_SQL)
        conn.commit()
    finally:
        conn.close()
