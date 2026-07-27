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


def write_claim_disposition(
    *,
    claim_id: str,
    disposition: str,
    decided_at: str,
    override_verdict: str | None = None,
    note: str | None = None,
    db_path: str | Path | None = None,
) -> bool:
    """Upsert the analyst's disposition for `claim_id`. Returns False (writing nothing) if the
    claim isn't in the store — defense-in-depth against a FK violation."""
    path = str(db_path or os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH)))
    with closing(connect(path)) as conn, conn:
        if conn.execute(
            "SELECT 1 FROM deduction_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone() is None:
            return False
        conn.execute(
            "INSERT INTO claim_dispositions "
            "(claim_id, disposition, override_verdict, note, decided_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(claim_id) DO UPDATE SET "
            "disposition = excluded.disposition, override_verdict = excluded.override_verdict, "
            "note = excluded.note, decided_at = excluded.decided_at",
            (claim_id, disposition, override_verdict, note, decided_at),
        )
    return True
