"""Read-side DB queries for the dashboard/worklist UI.

Reads the relational store the ETL builds (via mcp_server.db.connect + call-time DEDUCTIONS_DB,
same convention as FixtureLoader). No writes here — the pipeline owns resolution writes.
"""

import os
import sqlite3
from contextlib import closing
from datetime import date
from pathlib import Path

from mcp_server.db import DEFAULT_DB_PATH, connect

_PRIORITY_HIGH_CENTS = 15000  # >= $150 at risk -> HIGH
_PRIORITY_MED_CENTS = 5000    # >= $50 -> MEDIUM
_PRIORITY_AGE_DAYS = 45       # older than this -> HIGH regardless of amount


def _db_path() -> str:
    return os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH))


def _age_days(claim_date: str, ref_date: str) -> int:
    """Days between a claim and the lot's load date. Shared so priority and the oldest-open metric
    measure age the same way."""
    return (date.fromisoformat(ref_date) - date.fromisoformat(claim_date)).days


def priority(amount_cents: int, claim_date: str, ref_date: str) -> str:
    """Derive worklist priority from dollars at risk + aging (ref_date = the lot's load date)."""
    if amount_cents >= _PRIORITY_HIGH_CENTS or _age_days(claim_date, ref_date) > _PRIORITY_AGE_DAYS:
        return "HIGH"
    if amount_cents >= _PRIORITY_MED_CENTS:
        return "MEDIUM"
    return "LOW"


def active_batch() -> dict | None:
    """The current lot = the batch with the latest load_date."""
    with closing(connect(_db_path())) as conn:
        row = conn.execute(
            "SELECT batch_id, status, load_date FROM batches ORDER BY load_date DESC LIMIT 1"
        ).fetchone()
    return {"batch_id": row[0], "status": row[1], "load_date": row[2]} if row else None


def batch_exists(batch_id: str) -> bool:
    with closing(connect(_db_path())) as conn:
        return conn.execute("SELECT 1 FROM batches WHERE batch_id = ?", (batch_id,)).fetchone() is not None


def claim_exists(claim_id: str) -> bool:
    with closing(connect(_db_path())) as conn:
        return conn.execute(
            "SELECT 1 FROM deduction_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone() is not None


def agent_verdict(claim_id: str) -> str | None:
    """The agents' current verdict, or None if the claim hasn't been investigated.

    None is what lets the API separate 404 (unknown claim) from 409 (nothing to accept yet); the
    verdict itself is what lets it reject an "override" to the verdict the agents already gave.
    """
    with closing(connect(_db_path())) as conn:
        row = conn.execute(
            "SELECT final_verdict FROM claim_resolutions WHERE claim_id = ?", (claim_id,)
        ).fetchone()
    return row[0] if row else None


def _batch_load_date(conn, batch_id: str) -> str:
    return conn.execute("SELECT load_date FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()[0]


# The effective verdict: the analyst's decision when they have made one, otherwise the agents'.
#
# This is the single idea that keeps the dashboard coherent now that there are two verdict spines.
# `claim_resolutions` records what the AI concluded and is never rewritten by a human — that is the
# audit trail, and the whole reason it is a separate table from `claim_dispositions`. So "what is
# this claim's status" has to be *derived* at read time rather than stored, or the human's decision
# is invisible to every view (which is exactly the bug this replaced: accepting a verdict changed
# nothing on screen because every KPI and filter read only the agent's row).
#
# Layer 34 replaced a three-arm CASE with this COALESCE. The old version resolved `accept` by falling
# through to `r.final_verdict` — a *pointer*, so re-investigating a decided claim silently changed
# what the analyst was recorded as having approved. `claim_dispositions.decided_verdict` now holds a
# snapshot for every disposition (see orchestrator/dispositions.py), which makes this expression both
# correct and NULL-total: a legacy row with no snapshot degrades to the old behaviour instead of
# erroring.
_EFFECTIVE_VERDICT = "COALESCE(d.decided_verdict, r.final_verdict)"

# A decision the agents have since moved past. Deliberately does NOT feed _EFFECTIVE_VERDICT: the
# human's recorded call stands until they revisit it, and the badge tells them the machine changed its
# mind. Silently adopting the new agent verdict is the bug above wearing a different hat.
_DECISION_STALE = (
    "(d.decided_run_id IS NOT NULL AND r.run_id IS NOT NULL AND d.decided_run_id <> r.run_id)"
)

# A human has settled the claim. 'escalate' is deliberately excluded: parking a claim for someone
# else is not deciding it. COALESCE is load-bearing — for a claim with no disposition row
# `d.disposition` is NULL, and `NOT (NULL IN (...))` is NULL, not true, so an un-decided claim would
# silently fail every "not yet decided" predicate and vanish from the queue.
_DECIDED = "COALESCE(d.disposition, '') IN ('accept', 'override')"
_NOT_DECIDED = "COALESCE(d.disposition, '') NOT IN ('accept', 'override')"

# Every status predicate above references both spines, so both joins are mandatory everywhere they
# are used — including the COUNT query, which used to join only resolutions.
_JOINS = (
    "LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id "
    "LEFT JOIN claim_dispositions d ON d.claim_id = c.claim_id"
)

# SQL status predicates for the worklist filter tabs. Every KPI is counted with the predicate of the
# tab its card links to, so the number on a card always equals the rows you get by clicking it.
#
# Layer 35 made the arithmetic close *by construction*. The previous set overlapped: `unresolved` was
# `r.claim_id IS NULL` and `resolved` included any decided claim, so a never-investigated claim the
# analyst overrode (legal since Layer 34 — source documents don't depend on an agent run) was counted
# in both, and no combination of cards summed to the lot. Now there are exactly two disjoint queues,
# and everything else is derived from them rather than defined alongside them.
#
# Two details are load-bearing rather than decorative:
#
#   * The `r.claim_id IS NULL` / `IS NOT NULL` split is what makes the two arms disjoint. Testing the
#     effective verdict alone would not: a never-investigated claim with disposition='escalate' has
#     decided_verdict='ESCALATE' (see orchestrator/dispositions.py::derive_decided_verdict), so it
#     would match both arms and be double-counted.
#   * COALESCE around the verdict comparison keeps the predicate NULL-total. claim_resolutions
#     .final_verdict is nullable, and `NULL = 'ESCALATE'` is NULL, not false — so `todo` would be NULL
#     for such a row, `NOT todo` would also be NULL, and the claim would fall out of *both* halves.
#     That is the same NULL trap documented on _DECIDED above, and it would silently reopen exactly
#     the class of bug this layer exists to close.
_NOT_INVESTIGATED = f"(r.claim_id IS NULL AND {_NOT_DECIDED})"
_AWAITING_MY_CALL = (
    f"(r.claim_id IS NOT NULL AND COALESCE({_EFFECTIVE_VERDICT}, '') = 'ESCALATE' "
    f"AND {_NOT_DECIDED})"
)
# The union is written as the union of the two constants, so the two halves cannot drift from the
# whole; `decided` is the complement, which makes todo + decided == the lot an identity.
_TODO = f"({_NOT_INVESTIGATED} OR {_AWAITING_MY_CALL})"
_STATUS_SQL = {
    # The analyst's queue, and its two halves.
    "todo": _TODO,
    # Never investigated, and no human call either — so there is no verdict to accept or override.
    "not_investigated": _NOT_INVESTIGATED,
    # The agents ran and asked for a human. Deciding it drains this, which is what makes the number
    # respond to working the queue.
    "awaiting_my_call": _AWAITING_MY_CALL,
    # Settled — by a human, or by agents who reached a verdict that wasn't "ask a human".
    "decided": f"NOT {_TODO}",
    # Orthogonal to the partition (a claim can be disputable and either decided or not), so it is a
    # tab with no card: the KPI-equals-tab-rows invariant does not apply to it.
    "disputable": f"{_EFFECTIVE_VERDICT} = 'INVALID'",
}
_SORT_SQL = {
    "claim_id": "c.claim_id",
    "amount": "c.claimed_amount DESC",
    # priority tracks the same drivers priority() uses: dollars at risk, then aging.
    "priority": "c.claimed_amount DESC, c.claim_date ASC",
}


def batch_claims(
    batch_id: str,
    offset: int = 0,
    limit: int = 25,
    status_filter: str = "all",
    sort: str = "claim_id",
    q: str | None = None,
) -> dict:
    """One page of the lot's claims, each with derived priority, agent status, and human disposition.

    `status_filter` ∈ {all} | the keys of `_STATUS_SQL`; `sort` ∈ {claim_id, amount, priority};
    `q` matches claim_id / retailer / po_id (case-insensitive substring).
    """
    where = ["c.batch_id = ?"]
    params: list = [batch_id]
    if status_filter in _STATUS_SQL:
        where.append(_STATUS_SQL[status_filter])
    if q:
        where.append("(c.claim_id LIKE ? OR c.retailer LIKE ? OR c.po_id LIKE ?)")
        like = f"%{q}%"
        params += [like, like, like]
    where_sql = " AND ".join(where)
    order_sql = _SORT_SQL.get(sort, _SORT_SQL["claim_id"])

    with closing(connect(_db_path())) as conn:
        ref_date = _batch_load_date(conn, batch_id)
        total = conn.execute(
            f"SELECT COUNT(*) FROM deduction_claims c {_JOINS} WHERE {where_sql}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT c.claim_id, c.po_id, c.retailer, c.claimed_reason, c.claimed_amount, "
            f"c.claim_date, {_EFFECTIVE_VERDICT}, r.final_verdict, d.disposition, d.override_verdict, "
            f"d.decided_verdict, d.note, d.decided_at, {_DECISION_STALE} "
            f"FROM deduction_claims c {_JOINS} "
            f"WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    claims = [
        {
            "claim_id": cid, "po_id": po, "retailer": retailer, "claimed_reason": reason,
            "claimed_amount": amount, "claim_date": cdate,
            "priority": priority(amount, cdate, ref_date),
            # `status` is the effective verdict (what the claim's answer actually is now);
            # `agent_status` keeps the AI's original answer so an override can show what it superseded.
            "status": effective or "unresolved",
            "agent_status": agent_verdict or "unresolved",
            "disposition": disp,
            "override_verdict": override_verdict,
            "decided_verdict": decided_verdict,
            # note/decided_at were already stored and never returned, so the UI could show only
            # "Your decision: accept" with no timestamp and no sight of what the analyst wrote.
            "note": note,
            "decided_at": decided_at,
            "decision_stale": bool(stale),
        }
        for cid, po, retailer, reason, amount, cdate, effective, agent_verdict, disp,
        override_verdict, decided_verdict, note, decided_at, stale in rows
    ]
    return {"batch_id": batch_id, "total": total, "offset": offset, "limit": limit, "claims": claims}


def unresolved_claim_ids(batch_id: str, cap: int | None = None) -> list[str]:
    """Still-unresolved claim ids for the batch — the bulk-run worklist. `cap=None` = the whole lot
    (the ingestion "process lot" path); a positive cap limits it."""
    sql = ("SELECT c.claim_id FROM deduction_claims c "
           "LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id "
           "WHERE c.batch_id = ? AND r.claim_id IS NULL ORDER BY c.claim_id")
    params: list = [batch_id]
    if cap is not None:
        sql += " LIMIT ?"
        params.append(cap)
    with closing(connect(_db_path())) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [row[0] for row in rows]


def claim_documents(claim_id: str) -> dict | None:
    """The claim's full source-document set straight from the DB — the analyst's primary evidence.

    Always available (unlike the agent's case_file.json): claim + PO + ASN(s) + invoice(s) +
    receiving record(s) + matching trade agreement(s) + prior claims on the same PO. Returns None
    if the claim doesn't exist. Note: `retailer_notes` / receiving `notes` are free text and must
    be rendered as data, not HTML, by the client.
    """
    with closing(connect(_db_path())) as conn:
        conn.row_factory = sqlite3.Row
        claim = conn.execute("SELECT * FROM deduction_claims WHERE claim_id = ?", (claim_id,)).fetchone()
        if claim is None:
            return None
        po_id = claim["po_id"]
        po = conn.execute("SELECT * FROM purchase_orders WHERE po_id = ?", (po_id,)).fetchone()
        asns = conn.execute("SELECT * FROM asns WHERE po_id = ? ORDER BY ship_date", (po_id,)).fetchall()
        invoices = conn.execute("SELECT * FROM invoices WHERE po_id = ?", (po_id,)).fetchall()
        receiving = conn.execute(
            "SELECT * FROM receiving_records WHERE po_id = ? ORDER BY receipt_date", (po_id,)
        ).fetchall()
        sku = po["sku"] if po else None
        agreements = conn.execute(
            "SELECT * FROM trade_agreements WHERE retailer = ? AND sku = ?",
            (claim["retailer"], sku),
        ).fetchall() if sku else []
        prior = conn.execute(
            "SELECT c.claim_id, c.claimed_reason, c.claimed_amount, c.claim_date, r.final_verdict "
            "FROM deduction_claims c LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id "
            "WHERE c.po_id = ? AND c.claim_id != ? ORDER BY c.claim_date",
            (po_id, claim_id),
        ).fetchall()

    d = dict  # shorthand: sqlite3.Row -> plain dict for JSON
    return {
        "claim": d(claim),
        "purchase_order": d(po) if po else None,
        "asns": [d(r) for r in asns],
        "invoices": [d(r) for r in invoices],
        "receiving_records": [d(r) for r in receiving],
        "trade_agreements": [d(r) for r in agreements],
        "prior_claims": [d(r) for r in prior],
    }


def dashboard_metrics() -> dict:
    """Headline metrics for the active lot.

    Every count is lot-scoped and counted with the predicate of the tab its card links to, so a card's
    number always equals the rows you get by clicking it. `todo_count` + `decided_count` == `lot_total`
    by construction (see _STATUS_SQL), and `not_investigated_count` + `awaiting_my_call_count` ==
    `todo_count`. There is deliberately no cross-lot month window any more: the old
    `resolved_this_month` was the one metric no tab could reproduce, so that card could never agree
    with its own rows.
    """
    batch = active_batch()
    if batch is None:
        return {"lot_total": 0, "todo_count": 0, "not_investigated_count": 0,
                "awaiting_my_call_count": 0, "decided_count": 0, "open_amount_cents": 0,
                "oldest_open_days": 0,
                "priority_breakdown": {"HIGH": 0, "MEDIUM": 0, "LOW": 0}, "batch": None}

    with closing(connect(_db_path())) as conn:

        def count(predicate: str) -> int:
            return conn.execute(
                f"SELECT COUNT(*) FROM deduction_claims c {_JOINS} "
                f"WHERE c.batch_id = ? AND {predicate}",
                (batch["batch_id"],),
            ).fetchone()[0]

        lot_total = count("1")
        todo_count = count(_STATUS_SQL["todo"])
        not_investigated_count = count(_STATUS_SQL["not_investigated"])
        awaiting_my_call_count = count(_STATUS_SQL["awaiting_my_call"])
        decided_count = count(_STATUS_SQL["decided"])
        # One pass over the open claims serves the money, the priority mix and the aging figure.
        open_claims = conn.execute(
            f"SELECT c.claimed_amount, c.claim_date FROM deduction_claims c {_JOINS} "
            f"WHERE c.batch_id = ? AND {_STATUS_SQL['todo']}",
            (batch["batch_id"],),
        ).fetchall()

    breakdown = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for amount, cdate in open_claims:
        breakdown[priority(amount, cdate, batch["load_date"])] += 1

    return {
        "lot_total": lot_total,
        "todo_count": todo_count,
        "not_investigated_count": not_investigated_count,
        "awaiting_my_call_count": awaiting_my_call_count,
        "decided_count": decided_count,
        "open_amount_cents": sum(amount for amount, _ in open_claims),
        # Age is measured against the lot's load date, which the client doesn't have — so it is
        # derived here rather than in the browser.
        "oldest_open_days": max(
            (_age_days(cdate, batch["load_date"]) for _, cdate in open_claims), default=0
        ),
        "priority_breakdown": breakdown,
        "batch": {"batch_id": batch["batch_id"], "status": batch["status"]},
    }
