"""Unit tests for orchestrator/resolutions.write_claim_resolution (Layer 29)."""

from mcp_server.db import connect, init_db
from orchestrator.resolutions import write_claim_resolution


def test_upsert_writes_and_refreshes(tmp_path):
    db = tmp_path / "r.db"
    init_db(db)
    with connect(db) as conn:  # a claim must exist (FK); insert a minimal PO + claim
        conn.execute("INSERT INTO purchase_orders (po_id) VALUES ('PO-1')")
        conn.execute("INSERT INTO deduction_claims (claim_id, po_id) VALUES ('CLM-1', 'PO-1')")

    assert write_claim_resolution(claim_id="CLM-1", investigator_verdict="VALID",
                                  final_verdict="VALID", confidence=0.9, resolved_at="t1",
                                  run_id="run-1", db_path=db) is True
    # re-run refreshes the same row (upsert), no duplicate
    write_claim_resolution(claim_id="CLM-1", investigator_verdict="INVALID", final_verdict="INVALID",
                           confidence=0.5, resolved_at="t2", run_id="run-2", db_path=db)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT final_verdict, run_id FROM claim_resolutions WHERE claim_id = 'CLM-1'"
        ).fetchall()
    assert rows == [("INVALID", "run-2")]


def test_unknown_claim_is_skipped(tmp_path):
    db = tmp_path / "r.db"
    init_db(db)
    assert write_claim_resolution(claim_id="CLM-404", investigator_verdict="VALID",
                                  final_verdict="VALID", confidence=1.0, resolved_at="t",
                                  run_id="r", db_path=db) is False
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM claim_resolutions").fetchone()[0] == 0
