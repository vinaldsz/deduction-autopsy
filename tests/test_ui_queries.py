"""Layer 30b: the dashboard read-queries (ui/queries.py) against a controlled temp DB.

Isolated from the shared session DB (which pipeline tests mutate) so counts are deterministic.
"""

from datetime import UTC, datetime

import pytest

from mcp_server.db import connect, init_db
from ui import queries


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "dash.db"
    init_db(path)
    now = datetime.now(UTC).isoformat()
    with connect(path) as conn:
        conn.execute("INSERT INTO batches (batch_id, load_date, status) VALUES ('LOT-2024-06-08','2024-06-08','complete')")
        conn.execute("INSERT INTO batches (batch_id, load_date, status) VALUES ('LOT-2024-09-15','2024-09-15','complete')")
        conn.execute("INSERT INTO purchase_orders (po_id) VALUES ('PO-T')")
        rows = [
            ("CLM-A", 20000, "2024-09-15"),  # HIGH (amount)
            ("CLM-B", 8000, "2024-09-15"),   # MEDIUM
            ("CLM-C", 1000, "2024-09-15"),   # LOW
            ("CLM-D", 1000, "2024-01-01"),   # HIGH (aged > 45d vs lot date)
            ("CLM-E", 20000, "2024-09-15"),  # resolved -> excluded from unresolved
        ]
        for cid, amt, cdate in rows:
            conn.execute(
                "INSERT INTO deduction_claims (claim_id, po_id, batch_id, retailer, claimed_reason, "
                "claimed_amount, claim_date) VALUES (?, 'PO-T', 'LOT-2024-09-15', 'walmart', "
                "'shortage', ?, ?)", (cid, amt, cdate))
        conn.execute(
            "INSERT INTO claim_resolutions (claim_id, final_verdict, resolved_at) "
            "VALUES ('CLM-E', 'INVALID', ?)", (now,))
    monkeypatch.setenv("DEDUCTIONS_DB", str(path))
    return path


def test_priority_thresholds():
    assert queries.priority(20000, "2024-09-15", "2024-09-15") == "HIGH"
    assert queries.priority(8000, "2024-09-15", "2024-09-15") == "MEDIUM"
    assert queries.priority(1000, "2024-09-15", "2024-09-15") == "LOW"
    assert queries.priority(1000, "2024-01-01", "2024-09-15") == "HIGH"  # aging


def test_active_batch_is_latest_load_date(db):
    assert queries.active_batch()["batch_id"] == "LOT-2024-09-15"


def test_batch_claims_paginates_with_priority_and_status(db):
    page = queries.batch_claims("LOT-2024-09-15", offset=0, limit=2)
    assert page["total"] == 5
    assert [c["claim_id"] for c in page["claims"]] == ["CLM-A", "CLM-B"]
    assert page["claims"][0]["priority"] == "HIGH"
    resolved = queries.batch_claims("LOT-2024-09-15", offset=4, limit=2)["claims"]
    assert resolved[0]["claim_id"] == "CLM-E" and resolved[0]["status"] == "INVALID"


def test_batch_claims_filters_by_status(db):
    # CLM-E is the only resolved claim (INVALID) in the fixture.
    resolved = queries.batch_claims("LOT-2024-09-15", status_filter="resolved")
    assert [c["claim_id"] for c in resolved["claims"]] == ["CLM-E"]
    assert resolved["total"] == 1

    disputable = queries.batch_claims("LOT-2024-09-15", status_filter="disputable")
    assert [c["claim_id"] for c in disputable["claims"]] == ["CLM-E"]

    unresolved = queries.batch_claims("LOT-2024-09-15", status_filter="unresolved")
    assert "CLM-E" not in [c["claim_id"] for c in unresolved["claims"]]
    assert unresolved["total"] == 4

    # "needs_me" = unresolved OR escalated. CLM-E is resolved INVALID (not escalated) -> excluded.
    needs_me = queries.batch_claims("LOT-2024-09-15", status_filter="needs_me")
    assert "CLM-E" not in [c["claim_id"] for c in needs_me["claims"]]
    assert needs_me["total"] == 4


def test_batch_claims_sorts_by_amount(db):
    page = queries.batch_claims("LOT-2024-09-15", sort="amount")
    amounts = [c["claimed_amount"] for c in page["claims"]]
    assert amounts == sorted(amounts, reverse=True)


def test_batch_claims_search_matches_claim_id(db):
    page = queries.batch_claims("LOT-2024-09-15", q="CLM-A")
    assert [c["claim_id"] for c in page["claims"]] == ["CLM-A"]
    assert page["total"] == 1


def test_batch_claims_includes_human_disposition(db):
    from orchestrator.dispositions import write_claim_disposition

    write_claim_disposition(claim_id="CLM-A", disposition="override",
                            override_verdict="VALID", decided_at="t", db_path=db)
    rows = {c["claim_id"]: c["disposition"] for c in queries.batch_claims("LOT-2024-09-15")["claims"]}
    assert rows["CLM-A"] == "override"
    assert rows["CLM-B"] is None


def _escalate(db, claim_id="CLM-D"):
    """Give a claim an agent ESCALATE verdict, the state that routes work to the analyst."""
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO claim_resolutions (claim_id, final_verdict, resolved_at) VALUES (?, ?, ?)",
            (claim_id, "ESCALATE", datetime.now(UTC).isoformat()),
        )


# --- effective verdict: the human's decision outranks the agents' ---------------------------------
#
# These pin the bug the analyst hit: dispositions were written but no view read them, so accepting a
# verdict changed nothing on screen and "Needs human review" could never fall.


def test_escalated_claim_awaits_a_human_until_decided(db):
    _escalate(db)
    ids = lambda f: [c["claim_id"] for c in queries.batch_claims("LOT-2024-09-15", status_filter=f)["claims"]]
    assert "CLM-D" in ids("escalated")
    assert "CLM-D" in ids("needs_me")
    assert "CLM-D" not in ids("resolved")


def test_accepting_settles_the_claim_and_drains_the_queue(db):
    """The analyst's core complaint: accepting a verdict must move the claim to Resolved."""
    from orchestrator.dispositions import write_claim_disposition

    _escalate(db)
    write_claim_disposition(claim_id="CLM-D", disposition="accept", decided_at="t", db_path=db)
    ids = lambda f: [c["claim_id"] for c in queries.batch_claims("LOT-2024-09-15", status_filter=f)["claims"]]
    assert "CLM-D" in ids("resolved")
    assert "CLM-D" not in ids("needs_me")
    assert "CLM-D" not in ids("escalated")


def test_override_replaces_the_effective_verdict_but_keeps_the_agent_verdict(db):
    from orchestrator.dispositions import write_claim_disposition

    _escalate(db)
    write_claim_disposition(claim_id="CLM-D", disposition="override",
                            override_verdict="INVALID", decided_at="t", db_path=db)
    row = next(c for c in queries.batch_claims("LOT-2024-09-15")["claims"] if c["claim_id"] == "CLM-D")
    assert row["status"] == "INVALID"        # effective: what the claim's answer is now
    assert row["agent_status"] == "ESCALATE"  # preserved for the audit trail
    ids = lambda f: [c["claim_id"] for c in queries.batch_claims("LOT-2024-09-15", status_filter=f)["claims"]]
    assert "CLM-D" in ids("disputable")
    assert "CLM-D" not in ids("needs_me")


def test_undecided_claims_are_not_dropped_by_the_not_decided_predicate(db):
    """Regression guard for a SQL NULL trap: `NOT (d.disposition IN (...))` is NULL — not true — for
    a claim with no disposition row, which would have silently emptied the analyst's whole queue."""
    _escalate(db)
    needs_me = queries.batch_claims("LOT-2024-09-15", status_filter="needs_me")
    assert needs_me["total"] == 4  # 3 un-investigated + the escalated one, none lost to NULL


def test_kpis_equal_the_row_counts_of_the_tabs_they_link_to(db):
    from orchestrator.dispositions import write_claim_disposition

    _escalate(db)
    metrics = queries.dashboard_metrics()
    assert metrics["needs_human_review"] == queries.batch_claims(
        "LOT-2024-09-15", status_filter="escalated")["total"]
    assert metrics["needs_me_count"] == queries.batch_claims(
        "LOT-2024-09-15", status_filter="needs_me")["total"]

    # ...and the KPI actually moves when the analyst works the claim.
    before = queries.dashboard_metrics()["needs_human_review"]
    write_claim_disposition(claim_id="CLM-D", disposition="accept", decided_at="t", db_path=db)
    assert queries.dashboard_metrics()["needs_human_review"] == before - 1


def test_resolved_this_month_counts_human_decisions_too(db):
    from orchestrator.dispositions import write_claim_disposition

    # CLM-D, not CLM-A: as of Layer 34 you cannot accept a claim with no agent verdict to accept,
    # and CLM-A has never been investigated. Its resolution is dated to a past month on purpose, so
    # that only the *human* decision falls inside the window — `_escalate` stamps resolved_at with
    # now, which would already put the claim in the count and make this assertion prove nothing.
    with connect(db) as conn:
        conn.execute("INSERT INTO claim_resolutions (claim_id, final_verdict, resolved_at) "
                     "VALUES ('CLM-D', 'ESCALATE', '2023-01-05T00:00:00+00:00')")
    before = queries.dashboard_metrics()["resolved_this_month"]
    write_claim_disposition(claim_id="CLM-D", disposition="accept",
                            decided_at=datetime.now(UTC).isoformat(), db_path=db)
    assert queries.dashboard_metrics()["resolved_this_month"] == before + 1


# --- Layer 34: the effective verdict reads the analyst's snapshot -----------------------------------

def test_effective_verdict_uses_the_accepted_snapshot_not_the_latest_agent_verdict(db):
    """Read-side half of the Layer 34 regression: what the analyst accepted is what the worklist
    shows, even after the agents change their mind."""
    from orchestrator.dispositions import write_claim_disposition
    from orchestrator.resolutions import write_claim_resolution

    write_claim_disposition(claim_id="CLM-E", disposition="accept", decided_at="t1", db_path=db)
    write_claim_resolution(claim_id="CLM-E", investigator_verdict="VALID", final_verdict="VALID",
                           confidence=0.9, resolved_at="t2", run_id="run-2", db_path=db)

    row = next(c for c in queries.batch_claims("LOT-2024-09-15", status_filter="all")["claims"]
               if c["claim_id"] == "CLM-E")
    assert row["status"] == "INVALID"        # what the analyst signed off on
    assert row["agent_status"] == "VALID"    # where the agents have since moved


def test_reinvestigation_after_a_decision_marks_it_stale(db):
    from orchestrator.dispositions import write_claim_disposition
    from orchestrator.resolutions import write_claim_resolution

    def stale_flag():
        return next(c for c in queries.batch_claims("LOT-2024-09-15", status_filter="all")["claims"]
                    if c["claim_id"] == "CLM-E")["decision_stale"]

    # The fixture seeds CLM-E's resolution with no run_id; give it one first, since a decision can
    # only be detected as stale if it recorded which run it approved.
    write_claim_resolution(claim_id="CLM-E", investigator_verdict="INVALID", final_verdict="INVALID",
                           confidence=0.9, resolved_at="t0", run_id="run-1", db_path=db)
    write_claim_disposition(claim_id="CLM-E", disposition="accept", decided_at="t1", db_path=db)
    assert stale_flag() is False
    write_claim_resolution(claim_id="CLM-E", investigator_verdict="VALID", final_verdict="VALID",
                           confidence=0.9, resolved_at="t2", run_id="run-2", db_path=db)
    assert stale_flag() is True


def test_legacy_override_row_without_a_snapshot_still_wins(db):
    """The only test pinning _add_snapshot_columns' backfill: a pre-Layer-34 override row (written
    with override_verdict and no decided_verdict) must keep overriding, not silently fall through."""
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO claim_dispositions (claim_id, disposition, override_verdict, decided_at) "
            "VALUES ('CLM-E', 'override', 'VALID', 't1')")
        conn.execute(
            "UPDATE claim_dispositions SET decided_verdict = override_verdict "
            "WHERE disposition = 'override'")

    row = next(c for c in queries.batch_claims("LOT-2024-09-15", status_filter="all")["claims"]
               if c["claim_id"] == "CLM-E")
    assert row["status"] == "VALID"


def test_batch_claims_returns_the_decision_note_and_timestamp(db):
    """Both columns were always stored and never returned, so the UI could only ever say
    "Your decision: accept" with no timestamp and no sight of what the analyst wrote."""
    from orchestrator.dispositions import write_claim_disposition

    write_claim_disposition(claim_id="CLM-E", disposition="override", override_verdict="VALID",
                            note="ASN supports the retailer", decided_at="2026-07-28T10:00:00+00:00",
                            db_path=db)
    row = next(c for c in queries.batch_claims("LOT-2024-09-15", status_filter="all")["claims"]
               if c["claim_id"] == "CLM-E")
    assert row["note"] == "ASN supports the retailer"
    assert row["decided_at"] == "2026-07-28T10:00:00+00:00"
    assert row["decided_verdict"] == "VALID"


def test_agent_verdict_separates_unknown_from_uninvestigated(db):
    assert queries.agent_verdict("CLM-E") == "INVALID"
    assert queries.agent_verdict("CLM-A") is None   # exists, never investigated
    assert queries.agent_verdict("CLM-404") is None


def test_unresolved_claim_ids_caps_and_excludes_resolved(db):
    assert queries.unresolved_claim_ids("LOT-2024-09-15", cap=3) == ["CLM-A", "CLM-B", "CLM-C"]
    assert "CLM-E" not in queries.unresolved_claim_ids("LOT-2024-09-15", cap=99)


def test_unresolved_claim_ids_no_cap_returns_whole_lot(db):
    # cap=None (the "process lot" path) returns every unresolved claim, CLM-E excluded (resolved).
    assert queries.unresolved_claim_ids("LOT-2024-09-15") == ["CLM-A", "CLM-B", "CLM-C", "CLM-D"]


def test_claim_documents_assembles_entity_graph(db):
    docs = queries.claim_documents("CLM-A")
    assert docs is not None
    assert docs["claim"]["claim_id"] == "CLM-A"
    assert docs["purchase_order"]["po_id"] == "PO-T"
    assert docs["asns"] == [] and docs["invoices"] == [] and docs["receiving_records"] == []
    # All CLM-* share PO-T, so prior_claims = the other four, with CLM-E carrying its INVALID verdict.
    prior = {p["claim_id"]: p["final_verdict"] for p in docs["prior_claims"]}
    assert "CLM-A" not in prior and prior["CLM-E"] == "INVALID"


def test_claim_documents_unknown_claim_is_none(db):
    assert queries.claim_documents("CLM-404") is None


def test_dashboard_metrics(db):
    m = queries.dashboard_metrics()
    assert m["batch"] == {"batch_id": "LOT-2024-09-15", "status": "complete"}
    assert m["unresolved_count"] == 4
    assert m["dollars_at_risk_cents"] == 30000  # A+B+C+D, E resolved
    assert m["priority_breakdown"] == {"HIGH": 2, "MEDIUM": 1, "LOW": 1}
    assert m["resolved_this_month"] == 1
    assert m["needs_human_review"] == 0  # CLM-E resolved INVALID, not ESCALATE
