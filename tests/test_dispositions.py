"""Unit tests for orchestrator/dispositions.write_claim_disposition (Layer 32, amended Layer 34)."""

from mcp_server.db import connect, init_db
from orchestrator.dispositions import write_claim_disposition, write_claim_dispositions
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


def test_derive_decided_verdict_matches_what_gets_stored(tmp_path):
    """ui/server.py reports the decided verdict in its response and the writer stores it. They share
    this function precisely so the response can't contradict the row — pin both ends."""
    from orchestrator.dispositions import derive_decided_verdict

    assert derive_decided_verdict("override", "VALID", "INVALID") == "VALID"
    assert derive_decided_verdict("escalate", None, "INVALID") == "ESCALATE"
    assert derive_decided_verdict("accept", None, "INVALID") == "INVALID"

    db = tmp_path / "d.db"
    _seed_claim(db, verdict="INVALID")
    for disposition, override in (("accept", None), ("override", "VALID"), ("escalate", None)):
        write_claim_disposition(claim_id="CLM-1", disposition=disposition,
                                override_verdict=override, note="n", decided_at="t", db_path=db)
        assert _row(db)[3] == derive_decided_verdict(disposition, override, "INVALID")


def test_escalate_snapshots_escalate(tmp_path):
    db = tmp_path / "d.db"
    _seed_claim(db, verdict="VALID")

    write_claim_disposition(claim_id="CLM-1", disposition="escalate", decided_at="t1", db_path=db)
    assert _row(db)[3] == "ESCALATE"


# --- Layer 38: bulk accept -------------------------------------------------------------------------

def _seed_lot(db, claims, batch_id="LOT-2024-09-15"):
    """A batch of claims, each optionally carrying the agents' verdict: [(claim_id, verdict|None)]."""
    init_db(db)
    with connect(db) as conn:
        conn.execute("INSERT OR IGNORE INTO batches (batch_id, load_date) VALUES (?, '2024-09-15')",
                     (batch_id,))
        conn.execute("INSERT OR IGNORE INTO purchase_orders (po_id) VALUES ('PO-1')")
        for claim_id, _ in claims:
            conn.execute("INSERT INTO deduction_claims (claim_id, po_id, batch_id) "
                         "VALUES (?, 'PO-1', ?)", (claim_id, batch_id))
    for claim_id, verdict in claims:
        if verdict is not None:
            write_claim_resolution(claim_id=claim_id, investigator_verdict=verdict,
                                   final_verdict=verdict, confidence=0.9, resolved_at="t0",
                                   run_id="run-1", db_path=db)


def test_bulk_accept_records_every_eligible_claim_under_one_timestamp(tmp_path):
    """One decision, one timestamp: the whole action has to be recoverable as a single sign-off."""
    db = tmp_path / "d.db"
    _seed_lot(db, [("CLM-1", "INVALID"), ("CLM-2", "VALID"), ("CLM-3", "INVALID")])

    results = write_claim_dispositions(
        claim_ids=["CLM-1", "CLM-2", "CLM-3"], decided_at="t1", db_path=db)

    assert results == {"CLM-1": "recorded", "CLM-2": "recorded", "CLM-3": "recorded"}
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT claim_id, disposition, decided_verdict, decided_at, decided_run_id "
            "FROM claim_dispositions ORDER BY claim_id").fetchall()
    assert rows == [("CLM-1", "accept", "INVALID", "t1", "run-1"),
                    ("CLM-2", "accept", "VALID", "t1", "run-1"),
                    ("CLM-3", "accept", "INVALID", "t1", "run-1")]


def test_bulk_accept_skips_a_claim_with_no_verdict_to_accept(tmp_path):
    db = tmp_path / "d.db"
    _seed_lot(db, [("CLM-1", "INVALID"), ("CLM-2", None)])

    results = write_claim_dispositions(claim_ids=["CLM-1", "CLM-2"], decided_at="t1", db_path=db)

    assert results == {"CLM-1": "recorded", "CLM-2": "not_investigated"}
    assert _row(db, "CLM-2") is None


def test_bulk_accept_refuses_an_escalated_verdict(tmp_path):
    """Accepting "the agents couldn't resolve this" would record the claim as *decided* with verdict
    ESCALATE — settled, while nothing was settled. The awaiting-my-call queue exists precisely
    because those need reading, so bulk hands them back instead of draining them."""
    db = tmp_path / "d.db"
    _seed_lot(db, [("CLM-1", "ESCALATE")])

    results = write_claim_dispositions(claim_ids=["CLM-1"], decided_at="t1", db_path=db)

    assert results == {"CLM-1": "unresolved_verdict"}
    assert _row(db, "CLM-1") is None


def test_bulk_accept_never_rewrites_a_decision_the_analyst_already_made(tmp_path):
    """The headline safeguard. Overwriting one claim deliberately is the single-claim endpoint's job;
    doing it to a multi-row selection would restamp decided_at and silently drop an override's note."""
    db = tmp_path / "d.db"
    _seed_lot(db, [("CLM-1", "VALID"), ("CLM-2", "VALID")])
    write_claim_disposition(claim_id="CLM-1", disposition="override", override_verdict="INVALID",
                            note="ASN proves the full shipment", decided_at="t0", db_path=db)

    results = write_claim_dispositions(claim_ids=["CLM-1", "CLM-2"], decided_at="t1", db_path=db)

    assert results == {"CLM-1": "already_decided", "CLM-2": "recorded"}
    # Every field of the existing decision, not just its disposition string: the note and the
    # timestamp are the parts a bulk restamp would have quietly destroyed.
    with connect(db) as conn:
        assert conn.execute(
            "SELECT disposition, override_verdict, note, decided_at, decided_verdict "
            "FROM claim_dispositions WHERE claim_id = 'CLM-1'").fetchone() == (
                "override", "INVALID", "ASN proves the full shipment", "t0", "INVALID")


def test_bulk_accept_reports_an_unknown_claim_and_writes_nothing_for_it(tmp_path):
    db = tmp_path / "d.db"
    _seed_lot(db, [("CLM-1", "VALID")])

    results = write_claim_dispositions(claim_ids=["CLM-1", "CLM-404"], decided_at="t1", db_path=db)

    assert results == {"CLM-1": "recorded", "CLM-404": "unknown_claim"}
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM claim_dispositions").fetchone()[0] == 1


def test_bulk_accept_cannot_reach_into_another_lot(tmp_path):
    """The endpoint is keyed by batch. Without this scoping that batch_id would be decorative, and a
    hand-rolled POST could accept verdicts in a lot the analyst isn't looking at."""
    db = tmp_path / "d.db"
    _seed_lot(db, [("CLM-1", "VALID")], batch_id="LOT-2024-09-15")
    _seed_lot(db, [("CLM-OLD", "VALID")], batch_id="LOT-2024-06-08")

    results = write_claim_dispositions(
        claim_ids=["CLM-1", "CLM-OLD"], decided_at="t1", batch_id="LOT-2024-09-15", db_path=db)

    assert results == {"CLM-1": "recorded", "CLM-OLD": "unknown_claim"}
    assert _row(db, "CLM-OLD") is None


def test_bulk_accept_dedupes_ids(tmp_path):
    """A repeated id must report once — not race itself inside the transaction and come back
    `already_decided` on its own write."""
    db = tmp_path / "d.db"
    _seed_lot(db, [("CLM-1", "VALID")])

    results = write_claim_dispositions(claim_ids=["CLM-1", "CLM-1"], decided_at="t1", db_path=db)

    assert results == {"CLM-1": "recorded"}


def test_bulk_accept_of_nothing_writes_nothing(tmp_path):
    db = tmp_path / "d.db"
    _seed_lot(db, [("CLM-1", "VALID")])
    assert write_claim_dispositions(claim_ids=[], decided_at="t1", db_path=db) == {}
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM claim_dispositions").fetchone()[0] == 0
