"""Unit tests for orchestrator/dispositions.write_claim_disposition (Layer 32, amended Layer 34)."""

from mcp_server.db import connect, init_db
from orchestrator.dispositions import write_claim_disposition
from orchestrator.resolutions import write_claim_resolution


def _seed_claim(db, *, claim_id="CLM-1", verdict=None, run_id="run-1"):
    """A minimal PO + claim (FK), and optionally the agents' verdict on it."""
    init_db(db)
    with connect(db) as conn:
        conn.execute("INSERT OR IGNORE INTO purchase_orders (po_id) VALUES ('PO-1')")
        conn.execute("INSERT INTO deduction_claims (claim_id, po_id) VALUES (?, 'PO-1')", (claim_id,))
    if verdict is not None:
        write_claim_resolution(claim_id=claim_id, investigator_verdict=verdict, final_verdict=verdict,
                               confidence=0.9, resolved_at="t0", run_id=run_id, db_path=db)


def _row(db, claim_id="CLM-1"):
    with connect(db) as conn:
        return conn.execute(
            "SELECT disposition, override_verdict, note, decided_verdict, decided_run_id "
            "FROM claim_dispositions WHERE claim_id = ?", (claim_id,)
        ).fetchone()


def test_upsert_writes_and_refreshes(tmp_path):
    db = tmp_path / "d.db"
    _seed_claim(db, verdict="INVALID")

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
    db = tmp_path / "d.db"
    _seed_claim(db, verdict="INVALID")

    write_claim_disposition(claim_id="CLM-1", disposition="accept", decided_at="t1", db_path=db)
    write_claim_resolution(claim_id="CLM-1", investigator_verdict="INVALID", final_verdict="INVALID",
                           confidence=0.9, resolved_at="t2", run_id="run-9", db_path=db)
    with connect(db) as conn:
        assert conn.execute(
            "SELECT disposition FROM claim_dispositions WHERE claim_id = 'CLM-1'"
        ).fetchone() == ("accept",)


# --- Layer 34: the decision is a snapshot, not a pointer -------------------------------------------

def test_accept_snapshots_the_agent_verdict_and_run(tmp_path):
    db = tmp_path / "d.db"
    _seed_claim(db, verdict="VALID", run_id="run-1")

    assert write_claim_disposition(claim_id="CLM-1", disposition="accept",
                                   decided_at="t1", db_path=db) is True
    assert _row(db) == ("accept", None, None, "VALID", "run-1")


def test_reinvestigation_does_not_rewrite_what_the_analyst_approved(tmp_path):
    """The headline regression for Layer 34.

    Before the snapshot, `accept` was resolved at read time by falling through to
    claim_resolutions.final_verdict. So re-investigating a decided claim silently changed what the
    analyst was recorded as having approved — the audit trail asserted a human sign-off that never
    happened. test_disposition_survives_resolution_upsert above did not catch it because it only
    asserts the disposition *string* survives, which it always did.
    """
    db = tmp_path / "d.db"
    _seed_claim(db, verdict="VALID", run_id="run-1")
    write_claim_disposition(claim_id="CLM-1", disposition="accept", decided_at="t1", db_path=db)

    # The agents change their mind on a later run.
    write_claim_resolution(claim_id="CLM-1", investigator_verdict="INVALID", final_verdict="INVALID",
                           confidence=0.9, resolved_at="t2", run_id="run-2", db_path=db)

    disposition, _, _, decided_verdict, decided_run_id = _row(db)
    assert disposition == "accept"
    assert decided_verdict == "VALID", "the accepted verdict must not drift with the agents"
    assert decided_run_id == "run-1", "the decision stays bound to the run it approved"


def test_accept_without_a_resolution_is_refused(tmp_path):
    """Accepting a verdict that doesn't exist is meaningless — and used to be possible."""
    db = tmp_path / "d.db"
    _seed_claim(db)  # no resolution

    assert write_claim_disposition(claim_id="CLM-1", disposition="accept",
                                   decided_at="t1", db_path=db) is False
    assert _row(db) is None


def test_override_without_a_resolution_is_allowed(tmp_path):
    """Deliberately asymmetric with accept: claim_documents() serves the source documents regardless
    of any agent run, so an analyst can rule on evidence the agents never saw."""
    db = tmp_path / "d.db"
    _seed_claim(db)  # no resolution

    assert write_claim_disposition(claim_id="CLM-1", disposition="override", override_verdict="INVALID",
                                   note="shipped short, ASN proves it", decided_at="t1",
                                   db_path=db) is True
    assert _row(db) == ("override", "INVALID", "shipped short, ASN proves it", "INVALID", None)


def test_escalate_snapshots_escalate(tmp_path):
    db = tmp_path / "d.db"
    _seed_claim(db, verdict="VALID")

    write_claim_disposition(claim_id="CLM-1", disposition="escalate", decided_at="t1", db_path=db)
    assert _row(db)[3] == "ESCALATE"
