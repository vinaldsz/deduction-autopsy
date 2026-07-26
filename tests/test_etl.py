"""Offline tests for the Layer 27 ETL Load (no LLM, no OpenRouter).

The headline is the **fidelity oracle**: after build_db(), every business row in the DB must equal
the corresponding frozen scenarios/*.json model field-for-field — so the JSON -> sources -> ETL -> DB
chain is provably lossless and the 8 ground-truth verdicts can't drift. Plus idempotency,
referential integrity, the batch gate, seeded resolutions, and lineage/audit assertions.
"""

import sqlite3
from pathlib import Path

import pytest
from pydantic import BaseModel

from mcp_server.db import connect, init_db
from mcp_server.models import (
    ASN,
    DeductionClaim,
    Invoice,
    PurchaseOrder,
    ReceivingRecord,
    TradeAgreement,
)
from semantic_layer.dq_report import build_dq_report
from semantic_layer.etl import build_db
from semantic_layer.transform import RejectRecord, TransformResult

SCENARIOS = Path(__file__).parent.parent / "scenarios"
BUSINESS_TABLES = {
    "purchase_orders": "po_id", "asns": "asn_id", "invoices": "invoice_id",
    "receiving_records": "receipt_id", "trade_agreements": "agreement_id", "deduction_claims": "claim_id",
}


def _frozen_entities():
    """(table, model_cls, pk_col, frozen_model) for every entity across the 8 scenarios."""
    specs = [
        ("po.json", "purchase_orders", PurchaseOrder, "po_id"),
        ("invoice.json", "invoices", Invoice, "invoice_id"),
        ("receiving_record.json", "receiving_records", ReceivingRecord, "receipt_id"),
    ]
    out = []
    for scenario in sorted(p for p in SCENARIOS.iterdir() if p.is_dir()):
        for filename, table, model, pk in specs:
            out.append((table, model, pk, model.model_validate_json((scenario / filename).read_text())))
        for asn_path in sorted(scenario.glob("asn*.json")):
            out.append(("asns", ASN, "asn_id", ASN.model_validate_json(asn_path.read_text())))
        ta = scenario / "trade_agreement.json"
        if ta.exists():
            out.append(("trade_agreements", TradeAgreement, "agreement_id",
                        TradeAgreement.model_validate_json(ta.read_text())))
        for claim_path in sorted(scenario.glob("*claim*.json")):
            out.append(("deduction_claims", DeductionClaim, "claim_id",
                        DeductionClaim.model_validate_json(claim_path.read_text())))
    return out


FROZEN = _frozen_entities()


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = tmp_path_factory.mktemp("etl") / "deductions.db"
    build_db(db_path=path)
    conn = sqlite3.connect(path)
    yield conn
    conn.close()


def _db_model(conn: sqlite3.Connection, table: str, model_cls: type[BaseModel], pk_col: str, pk: str):
    cols = list(model_cls.model_fields)  # model has no batch_id, so it's excluded from the compare
    row = conn.execute(f"SELECT {', '.join(cols)} FROM {table} WHERE {pk_col} = ?", (pk,)).fetchone()
    assert row is not None, f"{table}.{pk_col}={pk} missing from DB"
    return model_cls.model_validate(dict(zip(cols, row)))


# --- fidelity oracle -----------------------------------------------------------------------------

@pytest.mark.parametrize("table,model_cls,pk_col,frozen", FROZEN,
                         ids=[f"{t}-{getattr(m, pk)}" for t, _, pk, m in FROZEN])
def test_db_row_equals_frozen_scenario(db, table, model_cls, pk_col, frozen):
    assert _db_model(db, table, model_cls, pk_col, getattr(frozen, pk_col)) == frozen


def test_business_row_counts(db):
    counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in BUSINESS_TABLES}
    assert counts == {"purchase_orders": 8, "asns": 9, "invoices": 8,
                      "receiving_records": 8, "trade_agreements": 1, "deduction_claims": 10}


# --- referential integrity, batch gate, resolutions, lineage -------------------------------------

def test_referential_integrity_holds(db):
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_batch_gate(db):
    batches = db.execute("SELECT batch_id, status FROM batches ORDER BY batch_id").fetchall()
    assert batches == [("LOT-2024-06-08", "complete"), ("LOT-2024-08-10", "complete"),
                       ("LOT-2024-09-15", "complete")]
    by_batch = dict(db.execute(
        "SELECT batch_id, COUNT(*) FROM deduction_claims GROUP BY batch_id").fetchall())
    assert by_batch == {"LOT-2024-06-08": 1, "LOT-2024-08-10": 1, "LOT-2024-09-15": 8}
    today = db.execute("SELECT batch_id FROM deduction_claims WHERE claim_id = ?", ("CLM-001",)).fetchone()
    assert today[0] == "LOT-2024-09-15"


def test_only_prior_claims_are_seeded_resolved(db):
    rows = db.execute("SELECT claim_id, final_verdict FROM claim_resolutions ORDER BY claim_id").fetchall()
    assert rows == [("CLM-007a", "VALID"), ("CLM-008a", "VALID")]


def test_lineage_and_load_audit(db):
    assert db.execute("SELECT COUNT(*) FROM lineage").fetchone()[0] == 44
    assert db.execute("SELECT COUNT(*) FROM reject_rows").fetchone()[0] == 0
    src = db.execute("SELECT source_file FROM lineage WHERE entity_table = ? AND entity_pk = ?",
                     ("purchase_orders", "PO-001")).fetchone()
    assert src[0] == "erp/purchase_orders.csv"
    audit = db.execute("SELECT source, rows_read, rows_loaded, rows_rejected FROM load_audit "
                       "WHERE source = ?", ("carrier/asn_856.txt",)).fetchone()
    assert audit == ("carrier/asn_856.txt", 9, 9, 0)


# --- idempotency ---------------------------------------------------------------------------------

def _business_dump(conn: sqlite3.Connection) -> dict:
    return {t: conn.execute(f"SELECT * FROM {t} ORDER BY {pk}").fetchall()
            for t, pk in BUSINESS_TABLES.items()}


def test_rebuild_is_idempotent(tmp_path):
    path = tmp_path / "d.db"
    build_db(db_path=path)
    conn = sqlite3.connect(path)
    first = _business_dump(conn)
    meta_counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                   for t in ("lineage", "load_audit", "reject_rows", "batches", "claim_resolutions")}
    conn.close()

    build_db(db_path=path)  # second run onto the same DB
    conn = sqlite3.connect(path)
    assert _business_dump(conn) == first  # no dup, identical business data
    assert {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in meta_counts} == meta_counts  # metadata refreshed, not accumulated
    conn.close()


# --- quarantine (load-level unit test) -----------------------------------------------------------

def test_load_quarantines_rejects_and_marks_batch(tmp_path):
    from semantic_layer.load import load

    path = tmp_path / "q.db"
    init_db(path)
    manifest = {"sources": [{"file": "portal/claims_2024-09-15.json", "format": "portal_json",
                             "target": "deduction_claims", "lot_date": "2024-09-15"}]}
    reject = RejectRecord(target="deduction_claims", source_file="portal/claims_2024-09-15.json",
                          source_row_ref="record 0", raw={"claimId": "CLM-BAD"}, reason="bad amount: x")
    result = TransformResult(clean=[], rejects=[reject])
    report = build_dq_report([], result)

    conn = connect(path)
    with conn:
        load(conn, result, report, manifest)
    assert conn.execute("SELECT reason FROM reject_rows").fetchone()[0] == "bad amount: x"
    assert conn.execute("SELECT status FROM batches WHERE batch_id = ?",
                        ("LOT-2024-09-15",)).fetchone()[0] == "complete_with_exceptions"
    conn.close()
