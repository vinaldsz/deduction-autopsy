"""Human-decision persistence (Layer 32).

After an analyst reviews a claim in the UI they record a disposition — accept the agents' verdict,
override it, or send it to a human queue. Kept separate from `orchestrator/resolutions.py` (the
agents' verdict): this is the *human's* call, and re-investigating a claim UPSERTs the resolution
without touching the disposition here.
"""

import os
from contextlib import closing
from pathlib import Path

from mcp_server.db import DEFAULT_DB_PATH, connect


def derive_decided_verdict(
    disposition: str, override_verdict: str | None, agent_verdict: str | None
) -> str | None:
    """The verdict a disposition actually signs off on.

    Shared with ui/server.py so the API reports exactly what gets stored — the two derivations
    drifting apart would mean the response contradicts the row.
    """
    if disposition == "override":
        return override_verdict
    if disposition == "escalate":
        return "ESCALATE"
    return agent_verdict


def write_claim_disposition(
    *,
    claim_id: str,
    disposition: str,
    decided_at: str,
    override_verdict: str | None = None,
    note: str | None = None,
    db_path: str | Path | None = None,
) -> bool:
    """Upsert the analyst's disposition for `claim_id`, snapshotting the verdict they signed off on.

    Returns False (writing nothing) if the claim isn't in the store — defense-in-depth against a FK
    violation — or if this is an `accept` with no agent verdict to accept.
    """
    path = str(db_path or os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH)))
    with closing(connect(path)) as conn, conn:
        if conn.execute(
            "SELECT 1 FROM deduction_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone() is None:
            return False
        resolution = conn.execute(
            "SELECT final_verdict, run_id FROM claim_resolutions WHERE claim_id = ?", (claim_id,)
        ).fetchone()
        # Asymmetric on purpose: accepting a verdict that doesn't exist is meaningless, but
        # *overriding* without one is legitimate — claim_documents() serves the source documents
        # regardless of any agent run, so an analyst can rule on evidence the agents never saw.
        if disposition == "accept" and resolution is None:
            return False
        agent_verdict, run_id = resolution if resolution else (None, None)
        # The snapshot. Storing the agent's verdict rather than pointing at it is the whole change:
        # a later re-investigation must not retroactively rewrite what the human approved.
        decided_verdict = derive_decided_verdict(disposition, override_verdict, agent_verdict)
        conn.execute(
            "INSERT INTO claim_dispositions "
            "(claim_id, disposition, override_verdict, note, decided_at, decided_verdict, "
            "decided_run_id) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(claim_id) DO UPDATE SET "
            "disposition = excluded.disposition, override_verdict = excluded.override_verdict, "
            "note = excluded.note, decided_at = excluded.decided_at, "
            "decided_verdict = excluded.decided_verdict, decided_run_id = excluded.decided_run_id",
            (claim_id, disposition, override_verdict, note, decided_at, decided_verdict, run_id),
        )
    return True
