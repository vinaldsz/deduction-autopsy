"""Offline schema tests for mcp_server/db.py (no LLM, no OpenRouter).

Layer 23: proves the DDL creates a clean, empty DB matching the finalized schema, is
idempotent, and enforces foreign keys. This is the gate for Layers 24+.
"""

import sqlite3

import pytest

from mcp_server import db

EXPECTED_TABLES = {
    "purchase_orders",
    "asns",
    "invoices",
    "receiving_records",
    "trade_agreements",
    "deduction_claims",
    "batches",
    "claim_resolutions",
    "claim_dispositions",
    "reject_rows",
    "load_audit",
    "lineage",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall()
    return {r[0] for r in rows}


def _views(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'view'"
    ).fetchall()
    return {r[0] for r in rows}


EXPECTED_INDEXES = {
    "idx_asns_po_id",
    "idx_invoices_po_id",
    "idx_receiving_records_po_id",
    "idx_deduction_claims_po_id",
    "idx_deduction_claims_batch_id",
    "idx_trade_agreements_lookup",
    "idx_reject_rows_batch_id",
    "idx_load_audit_batch_id",
    "idx_lineage_batch_id",
    "idx_lineage_entity",
}


def _named_indexes(conn: sqlite3.Connection) -> set[str]:
    # Exclude SQLite's auto-generated PK indexes (sqlite_autoindex_*).
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r[0] for r in rows}


def test_init_db_creates_all_tables(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        assert _tables(conn) == EXPECTED_TABLES
        assert len(EXPECTED_TABLES) == 12
        assert _views(conn) == set()   # v_batch_summary was dropped in Layer 35
        assert _named_indexes(conn) == EXPECTED_INDEXES
    finally:
        conn.close()


def test_init_db_creates_a_clean_empty_db(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        for table in EXPECTED_TABLES:
            count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            assert count == 0, f"{table} should be empty in a fresh DB"
    finally:
        conn.close()


def test_init_db_is_idempotent(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)
    db.init_db(db_path)  # second run must not error

    conn = db.connect(db_path)
    try:
        assert _tables(conn) == EXPECTED_TABLES
        assert _views(conn) == set()   # v_batch_summary was dropped in Layer 35
    finally:
        conn.close()


def test_init_db_drops_the_legacy_batch_summary_view(tmp_path):
    """Layer 35 removes v_batch_summary. A fresh DB never has it, so only an *existing* one proves the
    DROP in the schema script actually reaches a store built by an earlier version."""
    db_path = tmp_path / "legacy_view.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute("CREATE VIEW v_batch_summary AS SELECT batch_id FROM deduction_claims")
        conn.commit()
        assert _views(conn) == {"v_batch_summary"}
    finally:
        conn.close()

    db.init_db(db_path)   # the removal, on a DB that already had the view

    conn = db.connect(db_path)
    try:
        assert _views(conn) == set()
        assert _tables(conn) == EXPECTED_TABLES   # and nothing else went with it
    finally:
        conn.close()


def test_add_column_shim_upgrades_an_existing_db(tmp_path):
    """Layer 34's only migration mechanism.

    SQLite has no `ADD COLUMN IF NOT EXISTS`, so the idempotent CREATE TABLE cannot reach a DB that
    already has the Layer 32 shape — and `build_db` upserts onto the existing file rather than
    recreating it. Without the shim an existing data/deductions.db fails every worklist query with
    `no such column: decided_verdict`, while the whole test suite stays green because conftest
    builds a fresh DB in a tmp dir. That asymmetry is exactly how this would have bitten in
    production only.
    """
    db_path = tmp_path / "legacy.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        # Rewind to the pre-Layer-34 shape, with one row of each disposition kind already recorded.
        conn.execute("ALTER TABLE claim_dispositions DROP COLUMN decided_verdict")
        conn.execute("ALTER TABLE claim_dispositions DROP COLUMN decided_run_id")
        conn.execute("INSERT INTO purchase_orders (po_id) VALUES ('PO-1')")
        for claim_id in ("CLM-1", "CLM-2"):
            conn.execute("INSERT INTO deduction_claims (claim_id, po_id) VALUES (?, 'PO-1')",
                         (claim_id,))
        conn.execute("INSERT INTO claim_dispositions (claim_id, disposition, override_verdict) "
                     "VALUES ('CLM-1', 'override', 'VALID')")
        conn.execute("INSERT INTO claim_dispositions (claim_id, disposition) VALUES ('CLM-2', 'accept')")
        conn.commit()
    finally:
        conn.close()

    db.init_db(db_path)   # the upgrade
    db.init_db(db_path)   # and still idempotent afterwards

    conn = db.connect(db_path)
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(claim_dispositions)")}
        assert {"decided_verdict", "decided_run_id"} <= columns
        backfilled = dict(conn.execute(
            "SELECT claim_id, decided_verdict FROM claim_dispositions").fetchall())
        # An existing override already carried the analyst's verdict, so it can be carried forward.
        assert backfilled["CLM-1"] == "VALID"
        # An existing accept never was a snapshot and cannot be truthfully backfilled — it stays
        # NULL and falls through to the agents' verdict, exactly as it behaved before.
        assert backfilled["CLM-2"] is None
    finally:
        conn.close()


def test_connect_enables_foreign_keys(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    finally:
        conn.close()


def test_check_constraint_rejects_bad_claimed_reason(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO purchase_orders (po_id) VALUES ('PO-001')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO deduction_claims (claim_id, po_id, claimed_reason) "
                "VALUES ('CLM-001', 'PO-001', 'not_a_reason')"
            )
    finally:
        conn.close()


def test_foreign_key_rejects_orphan_child(tmp_path):
    db_path = tmp_path / "deductions.db"
    db.init_db(db_path)

    conn = db.connect(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO asns (asn_id, po_id) VALUES ('ASN-001', 'PO-MISSING')"
            )
    finally:
        conn.close()
