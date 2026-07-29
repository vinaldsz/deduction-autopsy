"""Human-decision persistence (Layer 32).

After an analyst reviews a claim in the UI they record a disposition — accept the agents' verdict,
override it, or send it to a human queue. Kept separate from `orchestrator/resolutions.py` (the
agents' verdict): this is the *human's* call, and re-investigating a claim UPSERTs the resolution
without touching the disposition here.

Two callers, one writer (Layer 38): the single-claim endpoint and the bulk accept-only endpoint both
go through `_write`, so the snapshot rules can't diverge between working one claim and working fifty.
"""

import os
import sqlite3
from contextlib import closing
from pathlib import Path

from mcp_server.db import DEFAULT_DB_PATH, connect

# Per-claim outcomes. These reach the client (ui/server.py returns them verbatim in the bulk
# response, and ui/static/lib.js::bulkOutcomeSummary turns them into the analyst's sentence), so they
# are a stable vocabulary rather than internal detail.
RECORDED = "recorded"
UNKNOWN_CLAIM = "unknown_claim"
NOT_INVESTIGATED = "not_investigated"
# The two below are bulk-only policies — see write_claim_dispositions.
UNRESOLVED_VERDICT = "unresolved_verdict"
ALREADY_DECIDED = "already_decided"


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


def _claim_exists(conn: sqlite3.Connection, claim_id: str, batch_id: str | None) -> bool:
    """Is this claim in the store — and, when a batch is named, in *that* lot?

    The batch scoping is what stops a batch-keyed endpoint reaching into another lot's claim, which
    would make the `batch_id` in its URL decorative.
    """
    sql = "SELECT 1 FROM deduction_claims WHERE claim_id = ?"
    params: list = [claim_id]
    if batch_id is not None:
        sql += " AND batch_id = ?"
        params.append(batch_id)
    return conn.execute(sql, params).fetchone() is not None


def _write(
    conn: sqlite3.Connection,
    *,
    claim_id: str,
    disposition: str,
    decided_at: str,
    override_verdict: str | None = None,
    note: str | None = None,
    batch_id: str | None = None,
) -> str:
    """Write one disposition on an open connection, returning its outcome.

    Holds only the rules that are true of *every* caller: the claim has to exist (optionally within
    a given batch), and an `accept` needs a verdict to accept. Caller-specific policy — bulk's
    refusal to overwrite a decision or to accept an ESCALATE — deliberately lives in the caller, not
    behind a flag here, so the single-claim path's behaviour can't be changed by passing a boolean.
    """
    if not _claim_exists(conn, claim_id, batch_id):
        return UNKNOWN_CLAIM

    resolution = conn.execute(
        "SELECT final_verdict, run_id FROM claim_resolutions WHERE claim_id = ?", (claim_id,)
    ).fetchone()
    # Asymmetric on purpose: accepting a verdict that doesn't exist is meaningless, but
    # *overriding* without one is legitimate — claim_documents() serves the source documents
    # regardless of any agent run, so an analyst can rule on evidence the agents never saw.
    if disposition == "accept" and resolution is None:
        return NOT_INVESTIGATED
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
    return RECORDED


def _resolve_db_path(db_path: str | Path | None) -> str:
    return str(db_path or os.environ.get("DEDUCTIONS_DB", str(DEFAULT_DB_PATH)))


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
    with closing(connect(_resolve_db_path(db_path))) as conn, conn:
        return _write(
            conn, claim_id=claim_id, disposition=disposition, decided_at=decided_at,
            override_verdict=override_verdict, note=note,
        ) == RECORDED


def write_claim_dispositions(
    *,
    claim_ids: list[str],
    decided_at: str,
    batch_id: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, str]:
    """Accept the agents' verdict on many claims at once. Returns {claim_id: outcome}.

    **Accept only.** There is no `disposition` parameter, because a bulk *override* is the same
    "approved something they never saw" failure the Layer 33–41 phase exists to remove.

    One connection, one transaction and one `decided_at`, so the whole action is recoverable as a
    single decision in the audit trail. Two policies are bulk-specific and live here rather than in
    `_write`:

      * An **ESCALATE** verdict is refused (`unresolved_verdict`). Accepting "the agents couldn't
        resolve this" would record the claim as *decided* with verdict ESCALATE — settled, while
        nothing was settled. The awaiting-my-call queue exists precisely because those need reading.
      * A claim that **already carries a decision** is skipped (`already_decided`), never rewritten.
        Overwriting one claim deliberately is what the single-claim endpoint is for; doing it to a
        multi-row selection would silently restamp `decided_at` and drop an existing override's note.

    "Best effort per claim" means ineligible claims are *skipped and reported* — it does not mean
    errors are swallowed. A real sqlite3 error rolls the whole transaction back and propagates,
    because a partially-applied bulk decision reported as success is the worst available outcome.
    """
    # Order-preserving dedupe: a repeated id must report once, not race itself inside the
    # transaction and report a spurious `already_decided` on its own write.
    unique_ids = list(dict.fromkeys(claim_ids))
    results: dict[str, str] = {}
    with closing(connect(_resolve_db_path(db_path))) as conn, conn:
        for claim_id in unique_ids:
            # Membership first: a claim from another lot must read as unknown here, not have its
            # decision state reported back through a batch it doesn't belong to.
            if not _claim_exists(conn, claim_id, batch_id):
                results[claim_id] = UNKNOWN_CLAIM
                continue
            existing = conn.execute(
                "SELECT 1 FROM claim_dispositions WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            if existing is not None:
                results[claim_id] = ALREADY_DECIDED
                continue
            verdict = conn.execute(
                "SELECT final_verdict FROM claim_resolutions WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            if verdict is not None and verdict[0] == "ESCALATE":
                results[claim_id] = UNRESOLVED_VERDICT
                continue
            results[claim_id] = _write(
                conn, claim_id=claim_id, disposition="accept", decided_at=decided_at,
                batch_id=batch_id,
            )
    return results
