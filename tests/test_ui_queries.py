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


def test_priority_reason_states_the_driver_priority_actually_used():
    """The reason has to branch in the same order priority() does, or a pill can say HIGH while the
    line under it explains a rule that wasn't the one that fired."""
    assert queries.priority_reason(20000, "2024-09-15", "2024-09-15") == "$200.00 at risk"
    assert queries.priority_reason(1000, "2024-01-01", "2024-09-15") == "aged 258 days"
    assert queries.priority_reason(8000, "2024-09-15", "2024-09-15") == "$80.00 at risk"
    assert "routine" in queries.priority_reason(1000, "2024-09-15", "2024-09-15")
    # Over the HIGH amount *and* aged: priority() tests amount first, so the reason must too.
    assert queries.priority_reason(20000, "2024-01-01", "2024-09-15") == "$200.00 at risk"


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
    # CLM-E is the only claim with an agent verdict (INVALID) in the fixture.
    decided = queries.batch_claims("LOT-2024-09-15", status_filter="decided")
    assert [c["claim_id"] for c in decided["claims"]] == ["CLM-E"]
    assert decided["total"] == 1

    disputable = queries.batch_claims("LOT-2024-09-15", status_filter="disputable")
    assert [c["claim_id"] for c in disputable["claims"]] == ["CLM-E"]

    not_investigated = queries.batch_claims("LOT-2024-09-15", status_filter="not_investigated")
    assert "CLM-E" not in [c["claim_id"] for c in not_investigated["claims"]]
    assert not_investigated["total"] == 4

    # "todo" = not_investigated OR awaiting_my_call. CLM-E is INVALID (settled) -> excluded.
    todo = queries.batch_claims("LOT-2024-09-15", status_filter="todo")
    assert "CLM-E" not in [c["claim_id"] for c in todo["claims"]]
    assert todo["total"] == 4


def test_batch_claims_sorts_by_amount(db):
    page = queries.batch_claims("LOT-2024-09-15", sort="amount")
    amounts = [c["claimed_amount"] for c in page["claims"]]
    assert amounts == sorted(amounts, reverse=True)


def test_batch_claims_search_matches_claim_id(db):
    page = queries.batch_claims("LOT-2024-09-15", q="CLM-A")
    assert [c["claim_id"] for c in page["claims"]] == ["CLM-A"]
    assert page["total"] == 1


# --- Layer 37a: a sort you can page, filters you can narrow with, and a total you can trust --------


def _tied_amounts(db):
    """Two claims with identical amounts, inserted in *reverse* claim_id order.

    The reversal is the point. Without an explicit tiebreaker SQLite is free to return a tie group in
    any order and in practice returns it in rowid (= insertion) order, so a fixture inserted
    alphabetically hides the bug entirely. Inserting Z before Y makes the two orders disagree, which
    is what lets these tests fail on a partial ORDER BY instead of passing by luck.

    Kept out of the `db` fixture for the same reason `_every_edge_shape` is: the pagination and total
    assertions above are written against its 5 claims.
    """
    with connect(db) as conn:
        for cid in ("CLM-Z", "CLM-Y"):
            conn.execute(
                "INSERT INTO deduction_claims (claim_id, po_id, batch_id, retailer, claimed_reason, "
                "claimed_amount, claim_date) VALUES (?, 'PO-T', 'LOT-2024-09-15', 'kroger', "
                "'promo_billback', 7500, '2024-09-10')", (cid,))


def test_ties_are_broken_by_claim_id_so_the_order_is_total(db):
    """`ORDER BY claimed_amount DESC` alone is a *partial* order: the two 7500 claims can come back
    in either order, and which one you get is an implementation detail of the query plan."""
    _tied_amounts(db)
    page = queries.batch_claims("LOT-2024-09-15", sort="amount", limit=99)
    ids = [c["claim_id"] for c in page["claims"]]
    assert ids.index("CLM-Y") < ids.index("CLM-Z")
    # The tiebreaker is always ASC, so reversing the primary key must not reshuffle the tie group.
    reversed_ids = [c["claim_id"] for c in
                    queries.batch_claims("LOT-2024-09-15", sort="amount", direction="asc",
                                         limit=99)["claims"]]
    assert reversed_ids.index("CLM-Y") < reversed_ids.index("CLM-Z")


def test_paging_a_sorted_list_never_repeats_or_drops_a_claim(db):
    """The consequence the analyst actually sees: LIMIT/OFFSET re-runs the query once per page, so
    under a partial order a tied claim can appear on two consecutive pages while its twin appears
    on none.

    Honest scope: this test does *not* fail on the un-tiebroken query, because SQLite runs identical
    queries through an identical plan and so happens to return the same partial order every time.
    The guarantee was never promised, only observed — the test above is the one that catches the
    defect, and this one pins the invariant that guarantee exists to provide.
    """
    _tied_amounts(db)
    everything = {c["claim_id"] for c in
                  queries.batch_claims("LOT-2024-09-15", limit=99)["claims"]}
    walked = []
    for offset in range(len(everything)):
        walked += [c["claim_id"] for c in
                   queries.batch_claims("LOT-2024-09-15", sort="amount", offset=offset,
                                        limit=1)["claims"]]
    assert len(set(walked)) == len(walked)   # no claim shown twice
    assert set(walked) == everything         # and none lost between the pages


def test_direction_reverses_the_sort_and_is_whitelisted(db):
    desc = [c["claimed_amount"] for c in
            queries.batch_claims("LOT-2024-09-15", sort="amount", direction="desc")["claims"]]
    asc = [c["claimed_amount"] for c in
           queries.batch_claims("LOT-2024-09-15", sort="amount", direction="asc")["claims"]]
    assert desc == sorted(desc, reverse=True)
    assert asc == sorted(asc)
    # The string never reaches SQL — it is a key into _DIRECTIONS.
    with pytest.raises(ValueError, match="direction"):
        queries.batch_claims("LOT-2024-09-15", sort="amount", direction="DESC; DROP TABLE batches")


def test_each_sort_column_has_a_useful_default_direction(db):
    """Money and age are scanned biggest/oldest first; ids and names alphabetically. Stated as
    behaviour rather than as a dict lookup, so the defaults can't silently invert."""
    assert queries.batch_claims("LOT-2024-09-15", sort="amount")["direction"] == "desc"
    assert queries.batch_claims("LOT-2024-09-15", sort="age")["direction"] == "desc"
    assert queries.batch_claims("LOT-2024-09-15", sort="priority")["direction"] == "desc"
    assert queries.batch_claims("LOT-2024-09-15", sort="claim_id")["direction"] == "asc"
    assert queries.batch_claims("LOT-2024-09-15", sort="retailer")["direction"] == "asc"


def test_sorting_by_age_puts_the_oldest_claim_first(db):
    page = queries.batch_claims("LOT-2024-09-15", sort="age")
    assert page["claims"][0]["claim_id"] == "CLM-D"     # filed 2024-01-01
    assert page["claims"][0]["age_days"] == 258
    assert [c["age_days"] for c in page["claims"]] == sorted(
        [c["age_days"] for c in page["claims"]], reverse=True)


def test_priority_sort_groups_by_band_not_by_amount(db):
    """CLM-D is a $10 claim that is HIGH only because it is 258 days old. Under the old proxy sort
    (`claimed_amount DESC, claim_date ASC`) it landed below every MEDIUM claim — under a column
    header labelled Priority."""
    ids = [c["claim_id"] for c in queries.batch_claims("LOT-2024-09-15", sort="priority")["claims"]]
    assert ids.index("CLM-D") < ids.index("CLM-B")      # aged HIGH above a $80 MEDIUM
    bands = [c["priority"] for c in queries.batch_claims("LOT-2024-09-15", sort="priority")["claims"]]
    assert bands == sorted(bands, key=["LOW", "MEDIUM", "HIGH"].index, reverse=True)


def test_sql_priority_rank_agrees_with_the_python_priority_for_every_row(db):
    """The drift guard on the one duplicated rule in the module: _PRIORITY_RANK_SQL restates
    priority()'s thresholds in SQL. They are built from the same constants, but "built from the same
    constants" is not the same as "computes the same answer" — the julianday arithmetic and the
    boundary comparisons are separate implementations."""
    _tied_amounts(db)
    rank = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}
    ordered = queries.batch_claims("LOT-2024-09-15", sort="priority", limit=99)["claims"]
    ranks = [rank[queries.priority(c["claimed_amount"], c["claim_date"], "2024-09-15")]
             for c in ordered]
    assert ranks == sorted(ranks, reverse=True)
    # ...and every band the row reports is the band the SQL sorted it into.
    assert [rank[c["priority"]] for c in ordered] == ranks


def test_filters_by_retailer_reason_and_claim_date_range(db):
    _tied_amounts(db)   # two 'kroger' / 'promo_billback' claims dated 2024-09-10

    kroger = queries.batch_claims("LOT-2024-09-15", retailer="kroger")
    assert {c["claim_id"] for c in kroger["claims"]} == {"CLM-Y", "CLM-Z"}
    assert kroger["total"] == 2

    promo = queries.batch_claims("LOT-2024-09-15", reason="promo_billback")
    assert {c["claim_id"] for c in promo["claims"]} == {"CLM-Y", "CLM-Z"}

    # Inclusive on both ends: the boundary date is in the range, not next to it.
    window = queries.batch_claims("LOT-2024-09-15", date_from="2024-09-10", date_to="2024-09-10")
    assert {c["claim_id"] for c in window["claims"]} == {"CLM-Y", "CLM-Z"}
    assert "CLM-D" in {c["claim_id"] for c in           # 2024-01-01, before the window
                       queries.batch_claims("LOT-2024-09-15", date_to="2024-06-01")["claims"]}

    # Filters compose with each other and with the status predicates.
    both = queries.batch_claims("LOT-2024-09-15", retailer="kroger", status_filter="not_investigated")
    assert both["total"] == 2


def test_total_amount_is_over_the_filtered_set_not_the_page(db):
    """A footer that added up only the rows on screen would show a different total on every page —
    which is the one thing a total must never do."""
    page = queries.batch_claims("LOT-2024-09-15", limit=1)
    assert len(page["claims"]) == 1
    assert page["total"] == 5
    assert page["total_amount_cents"] == 50000   # 20000 + 8000 + 1000 + 1000 + 20000
    filtered = queries.batch_claims("LOT-2024-09-15", status_filter="decided", limit=1)
    assert filtered["total_amount_cents"] == 20000   # CLM-E alone
    empty = queries.batch_claims("LOT-2024-09-15", retailer="nobody")
    assert empty["total"] == 0 and empty["total_amount_cents"] == 0   # SUM of nothing is NULL


def test_claims_carry_their_age_and_why_they_are_prioritised(db):
    """Both are measured against the lot's load_date, which the browser never receives."""
    rows = {c["claim_id"]: c for c in queries.batch_claims("LOT-2024-09-15", limit=99)["claims"]}
    assert rows["CLM-D"]["age_days"] == 258
    assert rows["CLM-D"]["priority"] == "HIGH"
    assert rows["CLM-D"]["priority_reason"] == "aged 258 days"
    assert rows["CLM-A"]["age_days"] == 0
    assert rows["CLM-A"]["priority_reason"] == "$200.00 at risk"


def test_unknown_filter_sort_direction_and_dates_are_rejected(db):
    """Layer 35 left this as a known gap: an unrecognised value fell back to "all"/claim_id, so a
    stale link returned a plausible page of the wrong rows with nothing on screen saying so."""
    for kwargs, expected in [
        ({"status_filter": "needs_me"}, "status_filter"),
        ({"sort": "nope"}, "sort"),
        ({"direction": "sideways"}, "direction"),
        ({"date_from": "yesterday"}, "date_from"),
        ({"date_to": "2024-13-45"}, "date_to"),
    ]:
        with pytest.raises(ValueError, match=expected):
            queries.batch_claims("LOT-2024-09-15", **kwargs)


def test_page_bounds_are_rejected_rather_than_clamped(db):
    for kwargs, expected in [({"limit": 0}, "limit"), ({"limit": 5000}, "limit"),
                             ({"offset": -1}, "offset")]:
        with pytest.raises(ValueError, match=expected):
            queries.batch_claims("LOT-2024-09-15", **kwargs)


def test_lot_filter_options_lists_the_values_actually_in_the_lot(db):
    _tied_amounts(db)
    options = queries.lot_filter_options("LOT-2024-09-15")
    assert options == {"retailers": ["kroger", "walmart"],
                       "reasons": ["promo_billback", "shortage"]}
    # Scoped to the lot, not the whole store — the other batch has no claims at all.
    assert queries.lot_filter_options("LOT-2024-06-08") == {"retailers": [], "reasons": []}


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


def _ids(status_filter):
    return [c["claim_id"] for c in
            queries.batch_claims("LOT-2024-09-15", status_filter=status_filter)["claims"]]


def test_escalated_claim_awaits_a_human_until_decided(db):
    _escalate(db)
    assert "CLM-D" in _ids("awaiting_my_call")
    assert "CLM-D" in _ids("todo")
    assert "CLM-D" not in _ids("decided")


def test_accepting_settles_the_claim_and_drains_the_queue(db):
    """The analyst's core complaint: accepting a verdict must move the claim out of their queue."""
    from orchestrator.dispositions import write_claim_disposition

    _escalate(db)
    write_claim_disposition(claim_id="CLM-D", disposition="accept", decided_at="t", db_path=db)
    assert "CLM-D" in _ids("decided")
    assert "CLM-D" not in _ids("todo")
    assert "CLM-D" not in _ids("awaiting_my_call")


def test_override_replaces_the_effective_verdict_but_keeps_the_agent_verdict(db):
    from orchestrator.dispositions import write_claim_disposition

    _escalate(db)
    write_claim_disposition(claim_id="CLM-D", disposition="override",
                            override_verdict="INVALID", decided_at="t", db_path=db)
    row = next(c for c in queries.batch_claims("LOT-2024-09-15")["claims"] if c["claim_id"] == "CLM-D")
    assert row["status"] == "INVALID"        # effective: what the claim's answer is now
    assert row["agent_status"] == "ESCALATE"  # preserved for the audit trail
    assert "CLM-D" in _ids("disputable")
    assert "CLM-D" not in _ids("todo")


def test_undecided_claims_are_not_dropped_by_the_not_decided_predicate(db):
    """Regression guard for a SQL NULL trap: `NOT (d.disposition IN (...))` is NULL — not true — for
    a claim with no disposition row, which would have silently emptied the analyst's whole queue."""
    _escalate(db)
    todo = queries.batch_claims("LOT-2024-09-15", status_filter="todo")
    assert todo["total"] == 4  # 3 un-investigated + the escalated one, none lost to NULL


# --- Layer 35: the KPI arithmetic closes ------------------------------------------------------------
#
# The old predicates overlapped, so no combination of cards summed to the lot and one card (a
# cross-lot month window) could not be reproduced by any tab at all. These tests pin the partition
# itself, not just the individual numbers.


def _every_edge_shape(db):
    """Add the states a real lot contains and the base fixture doesn't, so the partition is exercised
    against every shape rather than only "investigated" vs "not".

    Kept out of the `db` fixture on purpose: the pagination/total assertions elsewhere are written
    against its 5 claims, and quietly widening it would weaken them.
    """
    from orchestrator.dispositions import write_claim_disposition

    with connect(db) as conn:
        for cid, verdict in [("CLM-F", "ESCALATE"), ("CLM-G", "ESCALATE"), ("CLM-H", None)]:
            conn.execute(
                "INSERT INTO deduction_claims (claim_id, po_id, batch_id, retailer, claimed_reason, "
                "claimed_amount, claim_date) VALUES (?, 'PO-T', 'LOT-2024-09-15', 'target', "
                "'shortage', 5000, '2024-09-15')", (cid,))
            # CLM-H's resolution carries a NULL final_verdict — the column is nullable, and NULL
            # comparisons are what make a naive predicate non-total.
            conn.execute(
                "INSERT INTO claim_resolutions (claim_id, final_verdict, resolved_at) "
                "VALUES (?, ?, 't')", (cid, verdict))
    # Never investigated, but overridden by the analyst on the source documents alone (legal since
    # Layer 34) — the row that used to be counted in BOTH "unresolved" and "resolved".
    write_claim_disposition(claim_id="CLM-A", disposition="override", override_verdict="INVALID",
                            note="ASN proves delivery", decided_at="t", db_path=db)
    # Escalated by the agents and accepted by the analyst -> settled.
    write_claim_disposition(claim_id="CLM-F", disposition="accept", decided_at="t", db_path=db)
    # Parked by the analyst for someone else: a disposition, but deliberately not a decision.
    write_claim_disposition(claim_id="CLM-G", disposition="escalate", decided_at="t", db_path=db)


def test_todo_and_decided_partition_the_lot(db):
    _every_edge_shape(db)
    m = queries.dashboard_metrics()
    todo, decided = set(_ids("todo")), set(_ids("decided"))
    everything = set(_ids("all"))

    assert todo | decided == everything      # total: no claim falls between the two halves
    assert todo & decided == set()           # disjoint: no claim is counted twice
    assert m["todo_count"] + m["decided_count"] == m["lot_total"] == len(everything)


def test_not_investigated_and_awaiting_my_call_are_disjoint_and_sum_to_todo(db):
    _every_edge_shape(db)
    m = queries.dashboard_metrics()
    fresh, awaiting = set(_ids("not_investigated")), set(_ids("awaiting_my_call"))

    assert fresh & awaiting == set()
    assert fresh | awaiting == set(_ids("todo"))
    assert m["not_investigated_count"] + m["awaiting_my_call_count"] == m["todo_count"]


def test_a_null_agent_verdict_still_lands_on_one_side_of_the_partition(db):
    """claim_resolutions.final_verdict is nullable, and `NULL = 'ESCALATE'` is NULL — not false. A
    predicate that isn't NULL-total would drop this claim from both halves, which is the same class of
    silent-disappearance bug as the _DECIDED NULL trap."""
    _every_edge_shape(db)
    assert "CLM-H" in set(_ids("todo")) | set(_ids("decided"))
    assert "CLM-H" not in set(_ids("todo")) & set(_ids("decided"))


def test_an_overridden_never_investigated_claim_is_counted_once(db):
    """The bug this layer exists to remove: CLM-A has no agent verdict but a human decision, so the
    old predicates put it in `unresolved` (r.claim_id IS NULL) AND `resolved` (decided) at once."""
    _every_edge_shape(db)
    assert "CLM-A" in _ids("decided")
    assert "CLM-A" not in _ids("todo")
    assert "CLM-A" not in _ids("not_investigated")


def test_parking_a_claim_is_not_deciding_it(db):
    """disposition='escalate' means "someone else should look at this", so the claim stays in the
    queue. It must not leak into the awaiting arm twice via its ESCALATE snapshot, either."""
    _every_edge_shape(db)
    assert "CLM-G" in _ids("todo")
    assert "CLM-G" not in _ids("decided")


def test_kpis_equal_the_row_counts_of_the_tabs_they_link_to(db):
    """Every number on the KPI strip is the count of the tab its card opens — including the two
    halves printed inside the To-do card."""
    from orchestrator.dispositions import write_claim_disposition

    _every_edge_shape(db)
    m = queries.dashboard_metrics()
    for key, status_filter in [("todo_count", "todo"), ("decided_count", "decided"),
                               ("not_investigated_count", "not_investigated"),
                               ("awaiting_my_call_count", "awaiting_my_call")]:
        assert m[key] == queries.batch_claims(
            "LOT-2024-09-15", status_filter=status_filter)["total"], key

    # ...and the numbers actually move when the analyst works a claim.
    before = queries.dashboard_metrics()
    _escalate(db)  # CLM-D now awaits the analyst
    write_claim_disposition(claim_id="CLM-D", disposition="accept", decided_at="t", db_path=db)
    after = queries.dashboard_metrics()
    assert after["todo_count"] == before["todo_count"] - 1
    assert after["decided_count"] == before["decided_count"] + 1
    assert after["lot_total"] == before["lot_total"]


def test_decided_count_is_lot_scoped(db):
    """Replaces the old cross-lot `resolved_this_month`, which no tab could reproduce: the card
    counted every lot and every month, while the tab it linked to showed only today's lot."""
    from orchestrator.dispositions import write_claim_disposition
    from orchestrator.resolutions import write_claim_resolution

    with connect(db) as conn:
        conn.execute(
            "INSERT INTO deduction_claims (claim_id, po_id, batch_id, retailer, claimed_reason, "
            "claimed_amount, claim_date) VALUES ('CLM-OLD', 'PO-T', 'LOT-2024-06-08', 'walmart', "
            "'shortage', 5000, '2024-06-08')")
    before = queries.dashboard_metrics()["decided_count"]
    write_claim_resolution(claim_id="CLM-OLD", investigator_verdict="VALID", final_verdict="VALID",
                           confidence=0.9, resolved_at=datetime.now(UTC).isoformat(),
                           run_id="r", db_path=db)
    write_claim_disposition(claim_id="CLM-OLD", disposition="accept",
                            decided_at=datetime.now(UTC).isoformat(), db_path=db)
    assert queries.dashboard_metrics()["decided_count"] == before


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


_METRIC_KEYS = {"lot_total", "todo_count", "not_investigated_count", "awaiting_my_call_count",
                "decided_count", "open_amount_cents", "oldest_open_days", "priority_breakdown",
                "priority_thresholds", "batch"}
# The non-numeric metrics, excluded from the "everything is 0 on an empty store" sweep below.
_NON_COUNT_KEYS = {"priority_breakdown", "priority_thresholds", "batch"}


def test_dashboard_metrics(db):
    m = queries.dashboard_metrics()
    assert set(m) == _METRIC_KEYS
    assert m["batch"] == {"batch_id": "LOT-2024-09-15", "status": "complete"}
    assert m["lot_total"] == 5
    assert m["todo_count"] == 4
    assert m["not_investigated_count"] == 4
    assert m["awaiting_my_call_count"] == 0  # CLM-E is INVALID, not ESCALATE
    assert m["decided_count"] == 1
    assert m["open_amount_cents"] == 30000  # A+B+C+D open, E settled
    assert m["priority_breakdown"] == {"HIGH": 2, "MEDIUM": 1, "LOW": 1}
    # CLM-D is dated 2024-01-01 against a lot loaded 2024-09-15 — the aged claim drives this.
    assert m["oldest_open_days"] == 258
    # Shipped so the queue can state its own banding rules instead of index.html retyping them.
    assert m["priority_thresholds"] == {"high_cents": 15000, "med_cents": 5000, "age_days": 45}


def test_dashboard_metrics_with_no_lot(monkeypatch, tmp_path):
    """The empty-store shape has to carry every key the UI reads, or a first run renders "undefined"
    across the strip."""
    init_db(tmp_path / "empty.db")
    monkeypatch.setenv("DEDUCTIONS_DB", str(tmp_path / "empty.db"))
    m = queries.dashboard_metrics()
    assert set(m) == _METRIC_KEYS  # the same shape as a populated lot, so no key reads "undefined"
    assert m["batch"] is None
    for key in _METRIC_KEYS - _NON_COUNT_KEYS:
        assert m[key] == 0
    assert m["priority_breakdown"] == {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    # The thresholds are constants, not counts — they must be present even with nothing loaded, or
    # the first-run legend renders "undefined".
    assert m["priority_thresholds"]["high_cents"] == 15000
