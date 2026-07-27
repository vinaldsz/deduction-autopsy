"""Pipeline-time resolution persistence.

After each investigation the pipeline records its verdict in `claim_resolutions` (the same table the
ETL seeds 007a/008a into), so the dashboard/`v_batch_summary` reflect what's been worked. Kept
separate from `semantic_layer/load.py` (which is ETL batch-load): this is the runtime write, and it
UPSERTs (re-investigating a claim refreshes its resolution), unlike the seed's non-clobbering insert.
"""

import os
from contextlib import closing
from pathlib import Path

from mcp_server.db import DEFAULT_DB_PATH, connect


def write_claim_resolution(
    *,
    claim_id: str,
    investigator_verdict: str,
    final_verdict: str,
    confidence: float,
    resolved_at: str,
    run_id: str,
    db_path: str | Path | None = None,
) -> bool:
    """Upsert a resolution row for `claim_id`. Returns False (writing nothing) if the claim isn't in
    the store — defense-in-depth against a FK violation; in the normal flow the claim always exists."""
    path = str(db_path or os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH)))
    with closing(connect(path)) as conn, conn:
        if conn.execute(
            "SELECT 1 FROM deduction_claims WHERE claim_id = ?", (claim_id,)
        ).fetchone() is None:
            return False
        conn.execute(
            "INSERT INTO claim_resolutions "
            "(claim_id, investigator_verdict, final_verdict, confidence, resolved_at, run_id) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(claim_id) DO UPDATE SET "
            "investigator_verdict = excluded.investigator_verdict, "
            "final_verdict = excluded.final_verdict, confidence = excluded.confidence, "
            "resolved_at = excluded.resolved_at, run_id = excluded.run_id",
            (claim_id, investigator_verdict, final_verdict, confidence, resolved_at, run_id),
        )
    return True
