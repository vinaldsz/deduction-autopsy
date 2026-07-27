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


def _batch_load_date(conn, batch_id: str) -> str:
    return conn.execute("SELECT load_date FROM batches WHERE batch_id = ?", (batch_id,)).fetchone()[0]


# SQL status predicates for the worklist filter tabs (applied against the resolution's
# final_verdict; "unresolved" = no resolution row yet).
_STATUS_SQL = {
    # "needs_me" = the analyst's actual queue: not yet investigated, or escalated for a human.
    "needs_me": "(r.claim_id IS NULL OR r.final_verdict = 'ESCALATE')",
    "unresolved": "r.claim_id IS NULL",
    "escalated": "r.final_verdict = 'ESCALATE'",
    "disputable": "r.final_verdict = 'INVALID'",
    "resolved": "r.claim_id IS NOT NULL",
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
            f"SELECT COUNT(*) FROM deduction_claims c "
            f"LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id WHERE {where_sql}",
            params,
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT c.claim_id, c.po_id, c.retailer, c.claimed_reason, c.claimed_amount, "
            "c.claim_date, r.final_verdict, d.disposition "
            "FROM deduction_claims c "
            "LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id "
            "LEFT JOIN claim_dispositions d ON d.claim_id = c.claim_id "
            f"WHERE {where_sql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    claims = [
        {
            "claim_id": cid, "po_id": po, "retailer": retailer, "claimed_reason": reason,
            "claimed_amount": amount, "claim_date": cdate,
            "priority": priority(amount, cdate, ref_date),
            "status": final_verdict or "unresolved",
            "disposition": disp,
        }
        for cid, po, retailer, reason, amount, cdate, final_verdict, disp in rows
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
                "needs_human_review": 0,
                "priority_breakdown": {"HIGH": 0, "MEDIUM": 0, "LOW": 0}, "batch": None}

    month_start = datetime.now(UTC).strftime("%Y-%m-01")
    with closing(connect(_db_path())) as conn:
        summary = conn.execute(
            "SELECT claims_total, claims_resolved, needs_human_review, dollars_at_risk_cents "
            "FROM v_batch_summary WHERE batch_id = ?", (batch["batch_id"],)
        ).fetchone() or (0, 0, 0, 0)
        resolved_this_month = conn.execute(
            "SELECT COUNT(*) FROM claim_resolutions WHERE resolved_at >= ?", (month_start,)
        ).fetchone()[0]
        unresolved = conn.execute(
            "SELECT c.claimed_amount, c.claim_date FROM deduction_claims c "
            "LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id "
            "WHERE c.batch_id = ? AND r.claim_id IS NULL", (batch["batch_id"],)
        ).fetchall()

    breakdown = {"HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for amount, cdate in unresolved:
        breakdown[priority(amount, cdate, batch["load_date"])] += 1

    claims_total, claims_resolved, needs_human_review, dollars_at_risk_cents = summary
    return {
        "unresolved_count": claims_total - claims_resolved,
        "resolved_this_month": resolved_this_month,
        "dollars_at_risk_cents": dollars_at_risk_cents or 0,
        "needs_human_review": needs_human_review or 0,
        "priority_breakdown": breakdown,
        "batch": {"batch_id": batch["batch_id"], "status": batch["status"]},
    }
