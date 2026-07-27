"""Unit tests for orchestrator/dispositions.write_claim_disposition (Layer 32)."""

from mcp_server.db import connect, init_db
from orchestrator.dispositions import write_claim_disposition


def test_upsert_writes_and_refreshes(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    with connect(db) as conn:  # a claim must exist (FK); insert a minimal PO + claim
        conn.execute("INSERT INTO purchase_orders (po_id) VALUES ('PO-1')")
        conn.execute("INSERT INTO deduction_claims (claim_id, po_id) VALUES ('CLM-1', 'PO-1')")

    assert write_claim_disposition(claim_id="CLM-1", disposition="accept",
                                   decided_at="t1", db_path=db) is True
    # re-deciding refreshes the same row (upsert), no duplicate
    write_claim_disposition(claim_id="CLM-1", disposition="override", override_verdict="VALID",
                            note="agent got the UOM wrong", decided_at="t2", db_path=db)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT disposition, override_verdict, note FROM claim_dispositions WHERE claim_id = 'CLM-1'"
        ).fetchall()
    assert rows == [("override", "VALID", "agent got the UOM wrong")]


def test_unknown_claim_is_skipped(tmp_path):
    db = tmp_path / "d.db"
    init_db(db)
    assert write_claim_disposition(claim_id="CLM-404", disposition="accept",
                                   decided_at="t", db_path=db) is False
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM claim_dispositions").fetchone()[0] == 0


def test_disposition_survives_resolution_upsert(tmp_path):
    """A human disposition and the agents' resolution live in separate tables; re-investigating
    (which UPSERTs claim_resolutions) must not touch the disposition."""
    from orchestrator.resolutions import write_claim_resolution

    db = tmp_path / "d.db"
    init_db(db)
    with connect(db) as conn:
        conn.execute("INSERT INTO purchase_orders (po_id) VALUES ('PO-1')")
        conn.execute("INSERT INTO deduction_claims (claim_id, po_id) VALUES ('CLM-1', 'PO-1')")

    write_claim_disposition(claim_id="CLM-1", disposition="accept", decided_at="t1", db_path=db)
    write_claim_resolution(claim_id="CLM-1", investigator_verdict="INVALID", final_verdict="INVALID",
                           confidence=0.9, resolved_at="t2", run_id="run-9", db_path=db)
    with connect(db) as conn:
        assert conn.execute(
            "SELECT disposition FROM claim_dispositions WHERE claim_id = 'CLM-1'"
        ).fetchone() == ("accept",)
