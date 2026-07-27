"""Read-side DB queries for the dashboard/worklist UI.

Reads the relational store the ETL builds (via mcp_server.db.connect + call-time DEDUCTIONS_DB,
same convention as FixtureLoader). No writes here — the pipeline owns resolution writes.
"""

import os
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


def batch_claims(batch_id: str, offset: int = 0, limit: int = 25) -> dict:
    """One page of the lot's claims, each with derived priority + resolution status."""
    with closing(connect(_db_path())) as conn:
        ref_date = _batch_load_date(conn, batch_id)
        total = conn.execute(
            "SELECT COUNT(*) FROM deduction_claims WHERE batch_id = ?", (batch_id,)
        ).fetchone()[0]
        rows = conn.execute(
            "SELECT c.claim_id, c.po_id, c.retailer, c.claimed_reason, c.claimed_amount, "
            "c.claim_date, r.final_verdict "
            "FROM deduction_claims c LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id "
            "WHERE c.batch_id = ? ORDER BY c.claim_id LIMIT ? OFFSET ?",
            (batch_id, limit, offset),
        ).fetchall()
    claims = [
        {
            "claim_id": cid, "po_id": po, "retailer": retailer, "claimed_reason": reason,
            "claimed_amount": amount, "claim_date": cdate,
            "priority": priority(amount, cdate, ref_date),
            "status": final_verdict or "unresolved",
        }
        for cid, po, retailer, reason, amount, cdate, final_verdict in rows
    ]
    return {"batch_id": batch_id, "total": total, "offset": offset, "limit": limit, "claims": claims}


def unresolved_claim_ids(batch_id: str, cap: int) -> list[str]:
    """Up to `cap` still-unresolved claim ids for the batch — the bulk-run worklist."""
    with closing(connect(_db_path())) as conn:
        rows = conn.execute(
            "SELECT c.claim_id FROM deduction_claims c "
            "LEFT JOIN claim_resolutions r ON r.claim_id = c.claim_id "
            "WHERE c.batch_id = ? AND r.claim_id IS NULL ORDER BY c.claim_id LIMIT ?",
            (batch_id, cap),
        ).fetchall()
    return [row[0] for row in rows]


def dashboard_metrics() -> dict:
    """Headline metrics for the active lot + resolved-this-month across all lots."""
    batch = active_batch()
    if batch is None:
        return {"unresolved_count": 0, "resolved_this_month": 0, "dollars_at_risk_cents": 0,
                "priority_breakdown": {"HIGH": 0, "MEDIUM": 0, "LOW": 0}, "batch": None}

    month_start = datetime.now(UTC).strftime("%Y-%m-01")
    with closing(connect(_db_path())) as conn:
        summary = conn.execute(
            "SELECT claims_total, claims_resolved, dollars_at_risk_cents FROM v_batch_summary "
            "WHERE batch_id = ?", (batch["batch_id"],)
        ).fetchone() or (0, 0, 0)
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

    claims_total, claims_resolved, dollars_at_risk_cents = summary
    return {
        "unresolved_count": claims_total - claims_resolved,
        "resolved_this_month": resolved_this_month,
        "dollars_at_risk_cents": dollars_at_risk_cents or 0,
        "priority_breakdown": breakdown,
        "batch": {"batch_id": batch["batch_id"], "status": batch["status"]},
    }
