"""Read-side DB queries for the dashboard/worklist UI.

Reads the relational store the ETL builds (via mcp_server.db.connect + call-time DEDUCTIONS_DB,
same convention as FixtureLoader). No writes here — the pipeline owns resolution writes.
"""

import os
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

from mcp_server.db import DEFAULT_DB_PATH, connect

_PRIORITY_HIGH_CENTS = 15000  # >= $150 at risk -> HIGH
_PRIORITY_MED_CENTS = 5000    # >= $50 -> MEDIUM
_PRIORITY_AGE_DAYS = 45       # older than this -> HIGH regardless of amount


def _db_path() -> str:
    return os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH))


def priority(amount_cents: int, claim_date: str, ref_date: str) -> str:
    """Derive worklist priority from dollars at risk + aging (ref_date = the lot's load date)."""
    age_days = (date.fromisoformat(ref_date) - date.fromisoformat(claim_date)).days
    if amount_cents >= _PRIORITY_HIGH_CENTS or age_days > _PRIORITY_AGE_DAYS:
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
_ESCALATED_AWAITING_HUMAN = f"({_EFFECTIVE_VERDICT} = 'ESCALATE' AND {_NOT_DECIDED})"
_STATUS_SQL = {
    # The analyst's actual queue: never investigated, or escalated and not yet decided.
    "needs_me": f"(r.claim_id IS NULL OR {_ESCALATED_AWAITING_HUMAN})",
    # Never investigated at all — so there is no agent verdict to accept or override.
    "unresolved": "r.claim_id IS NULL",
    # Reads as the card's label, "needs human review": still awaiting a human, not "ever escalated".
    # Deciding it drains this, which is what makes the number respond to working the queue.
    "escalated": _ESCALATED_AWAITING_HUMAN,
    "disputable": f"{_EFFECTIVE_VERDICT} = 'INVALID'",
    # Settled: either a human decided it, or the agents reached a verdict that wasn't "ask a human".
    "resolved": f"({_DECIDED} OR ({_EFFECTIVE_VERDICT} IS NOT NULL "
                f"AND {_EFFECTIVE_VERDICT} <> 'ESCALATE'))",
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

    `status_filter` ∈ {all, unresolved, escalated, disputable, resolved}; `sort` ∈ {claim_id,
    amount, priority}; `q` matches claim_id / retailer / po_id (case-insensitive substring).
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
    """Headline metrics for the active lot + resolved-this-month across all lots."""
    batch = active_batch()
    if batch is None:
        return {"unresolved_count": 0, "resolved_this_month": 0, "dollars_at_risk_cents": 0,
                "needs_human_review": 0, "needs_me_count": 0,
                "priority_breakdown": {"HIGH": 0, "MEDIUM": 0, "LOW": 0}, "batch": None}

    month_start = datetime.now(UTC).strftime("%Y-%m-01")
    with closing(connect(_db_path())) as conn:

        def count(predicate: str) -> int:
            return conn.execute(
                f"SELECT COUNT(*) FROM deduction_claims c {_JOINS} "
                f"WHERE c.batch_id = ? AND {predicate}",
                (batch["batch_id"],),
            ).fetchone()[0]

        # Counted with the same predicates the filter tabs use, so each KPI equals the number of
        # rows you get by clicking it. These deliberately no longer come from `v_batch_summary`:
        # that view only knows about `claim_resolutions`, so its needs_human_review could never
        # respond to an analyst's decision. The view is left untouched for the ETL/batch reporting
        # that owns it.
        unresolved_count = count(_STATUS_SQL["unresolved"])
        needs_human_review = count(_STATUS_SQL["escalated"])
        needs_me_count = count(_STATUS_SQL["needs_me"])
        # Settled this month by either spine — an accepted or overridden claim is worked, so it has
        # to count here or the number stays flat no matter how much the analyst gets through.
        resolved_this_month = conn.execute(
            "SELECT COUNT(*) FROM deduction_claims c "
            "LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id "
            "LEFT JOIN claim_dispositions d ON d.claim_id = c.claim_id "
            "WHERE r.resolved_at >= ? OR d.decided_at >= ?",
            (month_start, month_start),
        ).fetchone()[0]
        at_risk = conn.execute(
            f"SELECT c.claimed_amount, c.claim_date FROM deduction_claims c {_JOINS} "
            f"WHERE c.batch_id = ? AND NOT ({_STATUS_SQL['resolved']})",
            (batch["batch_id"],),
        ).fetchall()

    breakdown = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for amount, cdate in at_risk:
        breakdown[priority(amount, cdate, batch["load_date"])] += 1

    return {
        "unresolved_count": unresolved_count,
        "resolved_this_month": resolved_this_month,
        "dollars_at_risk_cents": sum(amount for amount, _ in at_risk),
        "needs_human_review": needs_human_review,
        "needs_me_count": needs_me_count,
        "priority_breakdown": breakdown,
        "batch": {"batch_id": batch["batch_id"], "status": batch["status"]},
    }
