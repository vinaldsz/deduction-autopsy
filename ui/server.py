"""FastAPI surface: the daily-lot dashboard + worklist over orchestrator.pipeline.run_pipeline.

Additive second entry point (the CLI is kept — see CLAUDE.md's "UI is additive"). Bound to
127.0.0.1 only, no auth, no rate limiting — same trust model as the CLI. Run with:

    uvicorn ui.server:app --host 127.0.0.1 --port 8000

Data comes from the relational store (ui/queries.py); "scenario" is retired (Layer 29/30).
"""

import asyncio
import json
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from statistics import median
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict

from agents.base import AgentRunnerError, ToolCallRecord
from orchestrator.dispositions import (
    RECORDED,
    derive_decided_verdict,
    write_claim_disposition,
    write_claim_dispositions,
)
from orchestrator.pipeline import PipelineError, PipelineResult, run_pipeline
from ui import queries

# Load .env so a locally-run `uvicorn ui.server:app` picks up OPENROUTER_API_KEY, matching the CLI.
load_dotenv()

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Where the pipeline archives per-run artifacts (matches run_pipeline's default output_dir).
OUTPUT_DIR = Path("outputs")

# How many claims may fail back-to-back before the lot run gives up. Per-claim recovery on its own
# turns one systemic fault (a dead OPENROUTER_API_KEY, OpenRouter down) into one paid failure per
# claim; a run of failures this long is a fault in the setup, not in the claims.
_MAX_CONSECUTIVE_FAILURES = 3

# asyncio keeps only a weak reference to a task, so a batch left running after the client
# disconnected could be garbage-collected mid-claim. Held until it finishes on its own.
_background_tasks: set[asyncio.Task] = set()

app = FastAPI(title="Deduction Autopsy")


def _case_file_summary(result: PipelineResult) -> dict:
    """The evidence bundle the review workspace renders: reconciliation, timeline, checklist.

    Both agents' `reasoning` rides along so a live run explains itself the same way a reload does
    (/casefile has always returned these two strings verbatim; only this summary dropped them).
    Costs ~1.3 KB of prose per claim on the batch `claim_done` event, which the batch runner does
    not read — bounded, and this server is localhost-only. Stripping `reasoning` from the
    *Reviewer's input* is an anti-anchoring prompt control (see CLAUDE.md) and says nothing about
    what the analyst may see.
    """
    cf = result.case_file
    ro = result.reviewer_output
    return {
        "po_summary": cf.po_summary.model_dump(),
        "timeline": [event.model_dump() for event in cf.timeline],
        "uom_conversions_applied": cf.uom_conversions_applied,
        "prior_claims": cf.prior_claims,
        "trade_agreement_found": cf.trade_agreement_found,
        "discrepancy_qty": cf.discrepancy_qty,
        "discrepancy_amount_cents": cf.discrepancy_amount_cents,
        "review_findings": ro.review_findings.model_dump(),
        "investigator_reasoning": cf.reasoning,
        "reviewer_reasoning": ro.reasoning,
    }


def _result_payload(result: PipelineResult) -> dict:
    """Final result shape shared by the single-claim `done` and the batch `claim_done` events.

    Carries the case-file summary inline so the workspace fills immediately after a live run
    without a follow-up round-trip to /casefile.
    """
    return {
        "claim_id": result.claim_id,
        "investigator_verdict": result.investigator_verdict,
        "reviewer_verdict": result.reviewer_verdict,
        "final_verdict": result.final_verdict,
        "confidence": result.confidence,
        "dispute_grounds": result.reviewer_output.dispute_grounds,
        "usage": result.usage,
        "case_file": _case_file_summary(result),
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# --- run artifacts on disk -------------------------------------------------------------------------
#
# OUTPUT_DIR is read inside these helpers rather than captured at import: the tests monkeypatch
# `server.OUTPUT_DIR`, and a default argument or module-level join would silently read the real
# outputs/ tree instead.


def _all_run_dirs() -> list[Path]:
    """Every archived run directory, across every claim — the sample the spend estimate is drawn
    from. Unordered; the estimate takes a median, which does not care."""
    if not OUTPUT_DIR.is_dir():
        return []
    return [run_dir for claim_dir in OUTPUT_DIR.iterdir() if claim_dir.is_dir()
            for run_dir in _run_dirs(claim_dir.name)]


def _run_tokens(usage: dict | None) -> int | None:
    """One run's total token spend, or None when it recorded none.

    Guarded per agent for the reason `usageLine` in lib.js is: a run with usage for only one of the
    two is a real shape on disk, and reading four levels deep unguarded threw inside a caller that
    swallowed it.
    """
    if not isinstance(usage, dict):
        return None
    total = 0
    found = False
    for agent in ("investigator", "reviewer"):
        side = usage.get(agent)
        if not isinstance(side, dict):
            continue
        for key in ("prompt_tokens", "completion_tokens"):
            value = side.get(key)
            if isinstance(value, int):
                total += value
                found = True
    return total if found else None


def _run_dirs(claim_id: str) -> list[Path]:
    """Every archived run directory for a claim, unordered.

    `iterdir()` + `is_dir()` because a claim directory is not uniformly run directories: pre-run-layout
    claims still have loose verdict.json / reasoning_trace.json / dispute_packet.md sitting beside
    them. `latest` is excluded **by name** — it is a symlink to one of the real run dirs, and
    `is_dir()` follows it, so counting it would report the newest run twice.
    """
    claim_dir = OUTPUT_DIR / claim_id
    if not claim_dir.is_dir():
        return []
    return [p for p in claim_dir.iterdir() if p.name != "latest" and p.is_dir()]


def _latest_run_id(claim_id: str) -> str | None:
    """Which run `latest` points at, or None when there is no symlink to read.

    `os.readlink` rather than `Path.resolve().name`: the link target is the bare run id, and resolve()
    on a plain directory named `latest` would return the string "latest" as if it were a run.
    """
    latest = OUTPUT_DIR / claim_id / "latest"
    return os.readlink(latest) if latest.is_symlink() else None


def _run_entry(run_dir: Path) -> dict:
    """One run's summary, from the verdict.json the pipeline already writes."""
    verdict = {}
    path = run_dir / "verdict.json"
    if path.exists():
        try:
            verdict = json.loads(path.read_text())
        except json.JSONDecodeError:
            logger.warning("unreadable_verdict_json path=%s", path)
    return {
        "run_id": run_dir.name,
        "timestamp": verdict.get("timestamp"),
        "investigator_verdict": verdict.get("investigator_verdict"),
        "reviewer_verdict": verdict.get("reviewer_verdict"),
        "final_verdict": verdict.get("final_verdict"),
        "confidence": verdict.get("confidence"),
        "usage": verdict.get("usage"),
        # A listed run is not necessarily rebuildable: runs predating Layer 32 wrote no case_file.json,
        # and only an INVALID verdict gets a dispute packet.
        "has_case_file": (run_dir / "case_file.json").exists(),
        "has_dispute_packet": (run_dir / "dispute_packet.md").exists(),
    }


@app.get("/api/dashboard")
async def dashboard():
    """Headline metrics for the active lot (to-do / decided / $ open / priority mix / aging)."""
    return queries.dashboard_metrics()


@app.get("/api/claims/{claim_id}/documents")
async def documents(claim_id: str):
    """The claim's source-document set from the DB (PO, ASNs, invoice, receiving, trade agreement,
    prior claims) — the analyst's primary evidence, always available regardless of agent runs."""
    docs = queries.claim_documents(claim_id)
    if docs is None:
        return JSONResponse(status_code=404, content={"error": f"unknown claim: {claim_id!r}"})
    return docs


@app.get("/api/claims/{claim_id}/casefile")
async def casefile(claim_id: str):
    """Full CaseFile + ReviewerOutput from the latest run, so the workspace can rebuild the
    evidence view for an already-investigated claim without re-running the agents."""
    if not queries.claim_exists(claim_id):
        return JSONResponse(status_code=404, content={"error": f"unknown claim: {claim_id!r}"})
    path = OUTPUT_DIR / claim_id / "latest" / "case_file.json"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": f"claim not yet investigated: {claim_id}"})
    return JSONResponse(content=json.loads(path.read_text()))


@app.get("/api/claims/{claim_id}/runs")
async def runs(claim_id: str):
    """Every archived run for a claim, newest first, so the analyst can see how the verdict has moved.

    Read-only history: there is no way to *open* an older run from here, on purpose. Rendering an old
    case file under the pane's current verdict chip would show one run's evidence beside another run's
    answer, and doing it honestly is Layer 41's work (see docs/PLAN.md).

    Ordered by verdict.json's `timestamp`, never by directory name: run ids are caller-supplied
    strings, and the repo's own `run-A`/`run-B` sort after every timestamp-shaped id while being the
    oldest runs on disk. A run with no readable timestamp sorts last, where a crashed run belongs.
    """
    if not queries.claim_exists(claim_id):
        return JSONResponse(status_code=404, content={"error": f"unknown claim: {claim_id!r}"})
    entries = [_run_entry(d) for d in _run_dirs(claim_id)]
    entries.sort(key=lambda e: (e["timestamp"] is not None, e["timestamp"] or ""), reverse=True)
    # A real claim with no runs is 200 with an empty list, not 404: "never investigated" is a true
    # answer, and the client asks this for every claim it opens.
    return {"claim_id": claim_id, "latest_run_id": _latest_run_id(claim_id), "runs": entries}


def _compact_trace(payload: dict) -> list[dict]:
    """reasoning_trace.json's raw message arrays -> the shape the live `tool_call` SSE event emits.

    Compacted rather than served raw for two reasons: the file runs ~28 KB and embeds both agents'
    system prompts verbatim, and `appendTrace` in the client already renders exactly this shape — so a
    past run's trace and a live one go through one renderer.

    Two details are a conversion, not a projection. `function.arguments` is a JSON *string* on disk
    while the live hook sends a dict, and `is_error` survives only as the "ERROR: " prefix that
    agents/base.py writes into the tool result — so it is read back from there rather than recomputed.
    """
    out: list[dict] = []
    for agent in ("investigator", "reviewer"):
        messages = payload.get(agent) or []
        # Which results errored, by tool_call_id, so a call can be marked from the reply that follows it.
        errored = {
            m.get("tool_call_id")
            for m in messages
            if m.get("role") == "tool" and str(m.get("content", "")).startswith("ERROR: ")
        }
        for message in messages:
            for call in message.get("tool_calls") or []:
                function = call.get("function") or {}
                try:
                    args = json.loads(function.get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                out.append(
                    {
                        "agent": agent,
                        "name": function.get("name"),
                        "args": args,
                        "is_error": call.get("id") in errored,
                    }
                )
    return out


@app.get("/api/claims/{claim_id}/trace")
async def trace(claim_id: str):
    """The latest run's tool-call trace, so the audit drawer works on a claim nobody just ran.

    CLAUDE.md's first non-negotiable is that MCP-only data access "is what makes the tool-call trace
    meaningful as an audit trail" — and until now the trace was readable only during the ~40 seconds it
    was being produced. The artifact was written every run and read by nothing.
    """
    if not queries.claim_exists(claim_id):
        return JSONResponse(status_code=404, content={"error": f"unknown claim: {claim_id!r}"})
    path = OUTPUT_DIR / claim_id / "latest" / "reasoning_trace.json"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": f"no trace recorded for {claim_id}"})
    return {"claim_id": claim_id, "tool_calls": _compact_trace(json.loads(path.read_text()))}


@app.get("/api/claims/{claim_id}/dispute-packet")
async def dispute_packet(claim_id: str):
    """The Markdown dispute packet for an INVALID claim's latest run (download)."""
    if not queries.claim_exists(claim_id):
        return JSONResponse(status_code=404, content={"error": f"unknown claim: {claim_id!r}"})
    path = OUTPUT_DIR / claim_id / "latest" / "dispute_packet.md"
    if not path.exists():
        return JSONResponse(status_code=404, content={"error": f"no dispute packet for {claim_id}"})
    return PlainTextResponse(
        path.read_text(),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="dispute_packet_{claim_id}.md"'},
    )


class DispositionBody(BaseModel):
    disposition: Literal["accept", "override", "escalate"]
    override_verdict: Literal["VALID", "INVALID", "ESCALATE"] | None = None
    note: str | None = None


@app.post("/api/claims/{claim_id}/disposition")
async def disposition(claim_id: str, body: DispositionBody):
    """Record the analyst's decision on a claim (accept / override / send to human).

    The verdict the analyst signs off on is snapshotted server-side (see
    orchestrator/dispositions.py), so re-investigating later cannot rewrite it. The request body
    still carries `override_verdict` — that's the client's *intent* — while the response reports the
    `decided_verdict` actually stored.
    """
    if not queries.claim_exists(claim_id):
        return JSONResponse(status_code=404, content={"error": f"unknown claim: {claim_id!r}"})

    agent_verdict = queries.agent_verdict(claim_id)
    if body.disposition == "accept" and agent_verdict is None:
        # Distinct from the 404 above: the claim exists, there is just no verdict to accept.
        return JSONResponse(status_code=409, content={
            "error": f"claim not yet investigated — nothing to accept: {claim_id}"})
    if body.disposition == "override":
        # An override with no stated reason is the one decision that most needs one: it is a human
        # overruling an audited verdict.
        if body.override_verdict is None:
            return JSONResponse(status_code=422, content={
                "error": "override requires an explicit override_verdict"})
        if not (body.note or "").strip():
            return JSONResponse(status_code=422, content={
                "error": "override requires a note explaining the reason"})
        if body.override_verdict == agent_verdict:
            return JSONResponse(status_code=422, content={
                "error": f"override_verdict matches the agents' verdict ({agent_verdict}) — "
                         "accept it instead"})

    decided_at = datetime.now(UTC).isoformat()
    write_claim_disposition(
        claim_id=claim_id,
        disposition=body.disposition,
        override_verdict=body.override_verdict,
        note=body.note,
        decided_at=decided_at,
    )
    # Same derivation the writer uses, so the response cannot contradict the row it just wrote.
    return {"claim_id": claim_id, "disposition": body.disposition,
            "override_verdict": body.override_verdict, "decided_at": decided_at,
            "decided_verdict": derive_decided_verdict(
                body.disposition, body.override_verdict, agent_verdict)}


@app.get("/api/batches/{batch_id}")
async def batch(
    batch_id: str,
    offset: int = 0,
    limit: int = 25,
    status_filter: str = "all",
    sort: str = "claim_id",
    direction: str | None = None,
    q: str | None = None,
    retailer: str | None = None,
    reason: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
):
    """One page of the lot's claims — filtered/sorted/searched for the triage queue."""
    if not queries.batch_exists(batch_id):
        return JSONResponse(status_code=404, content={"error": f"unknown batch: {batch_id!r}"})
    try:
        return queries.batch_claims(
            batch_id, offset=offset, limit=limit, status_filter=status_filter, sort=sort,
            direction=direction, q=q, retailer=retailer, reason=reason,
            date_from=date_from, date_to=date_to,
        )
    except ValueError as exc:
        # 422, not a silent fallback to "all"/claim_id: a page of plausible but wrong rows with
        # nothing on screen saying so is worse than an error. Distinct from the 404 above, which
        # is about the batch rather than the query.
        return JSONResponse(status_code=422, content={"error": str(exc)})


class BulkAcceptBody(BaseModel):
    # extra="forbid" is the enforcement, not decoration: a client posting
    # {"claim_ids": [...], "disposition": "override"} must be told no, not quietly bulk-accepted.
    model_config = ConfigDict(extra="forbid")

    claim_ids: list[str]


@app.post("/api/batches/{batch_id}/dispositions")
async def bulk_dispositions(batch_id: str, body: BulkAcceptBody):
    """Accept the agents' verdict on a selection of this lot's claims.

    **Accept only** — the body has no `disposition` field at all, because a bulk *override* is the
    same "approved something they never saw" failure this phase exists to remove. Always 200 (bar a
    bad batch or an empty selection) with a per-claim outcome map: some claims in a selection are
    legitimately ineligible (never investigated, agents said ESCALATE, already decided), and that is
    a result to report, not a request-level error. See orchestrator/dispositions.py for the rules.
    """
    if not queries.batch_exists(batch_id):
        return JSONResponse(status_code=404, content={"error": f"unknown batch: {batch_id!r}"})
    if not body.claim_ids:
        return JSONResponse(status_code=422, content={"error": "claim_ids must not be empty"})

    decided_at = datetime.now(UTC).isoformat()
    results = write_claim_dispositions(
        claim_ids=body.claim_ids, decided_at=decided_at, batch_id=batch_id)
    return {"batch_id": batch_id, "decided_at": decided_at,
            "recorded": sum(1 for outcome in results.values() if outcome == RECORDED),
            "results": results}


@app.get("/api/batches/{batch_id}/filter-options")
async def filter_options(batch_id: str):
    """The retailers and reasons present in this lot, for the queue's filter dropdowns."""
    if not queries.batch_exists(batch_id):
        return JSONResponse(status_code=404, content={"error": f"unknown batch: {batch_id!r}"})
    return queries.lot_filter_options(batch_id)


@app.post("/api/batches/{batch_id}/investigate")
async def investigate_batch(batch_id: str, cap: int | None = None):
    """Process the whole lot: run the pipeline over every unresolved claim, streaming progress.
    `cap` optionally limits it (defaults to the entire lot — the ingestion "process lot" step).

    One claim failing does not end the lot. `cli/process_lot.py` has always caught per claim and
    carried on; this endpoint used to wrap the entire loop in one `try`, so a failure on claim 7 of
    50 abandoned claims 8-50, threw away the tally and reported nothing but a raw exception string.
    """
    if not queries.batch_exists(batch_id):
        return JSONResponse(status_code=404, content={"error": f"unknown batch: {batch_id!r}"})

    claim_ids = queries.unresolved_claim_ids(batch_id, cap)

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()
        cancelled = asyncio.Event()

        def make_hook(agent: str, claim_id: str):
            def hook(record: ToolCallRecord) -> None:
                queue.put_nowait(_sse("tool_call", {
                    "claim_id": claim_id, "agent": agent, "name": record.name,
                    "args": record.args, "is_error": record.is_error,
                }))
            return hook

        async def run() -> None:
            tally = {"investigated": 0, "VALID": 0, "INVALID": 0, "ESCALATE": 0,
                     "failed": 0, "stopped_reason": None}
            consecutive = 0
            try:
                queue.put_nowait(_sse("batch_start", {"total": len(claim_ids)}))
                for claim_id in claim_ids:
                    # Checked between claims, never mid-claim: the in-flight run is already paid for
                    # and its run dir is half-written, and killing it would leave a run with no
                    # verdict.json that /runs would then list as unrebuildable.
                    if cancelled.is_set():
                        logger.info("ui_batch_cancelled batch_id=%s investigated=%s failed=%s",
                                    batch_id, tally["investigated"], tally["failed"])
                        return
                    # Which claim is being worked, said outright. The progress counter used to infer
                    # this from the first `tool_call`, so a claim that failed before reaching one
                    # never announced itself and the line went on naming the *previous* claim while
                    # the counter advanced past it.
                    queue.put_nowait(_sse("claim_start", {"claim_id": claim_id}))
                    try:
                        result = await run_pipeline(
                            claim_id=claim_id,
                            on_investigator_tool_call=make_hook("investigator", claim_id),
                            on_reviewer_tool_call=make_hook("reviewer", claim_id),
                        )
                    except Exception as exc:
                        # Broad on purpose, unlike the CLI's (PipelineError, AgentRunnerError): an
                        # unexpected TypeError on one claim must not cost the other 49.
                        logger.exception("ui_claim_failed batch_id=%s claim_id=%s", batch_id, claim_id)
                        tally["failed"] += 1
                        consecutive += 1
                        queue.put_nowait(_sse("claim_error", {"claim_id": claim_id, "error": str(exc)}))
                        if consecutive >= _MAX_CONSECUTIVE_FAILURES:
                            # A dead API key fails identically on every claim. Without this, per-claim
                            # resilience turns one systemic fault into 50 paid round-trips.
                            tally["stopped_reason"] = "consecutive_failures"
                            break
                        continue
                    consecutive = 0
                    tally["investigated"] += 1
                    tally[result.final_verdict] = tally.get(result.final_verdict, 0) + 1
                    queue.put_nowait(_sse("claim_done", _result_payload(result)))
                queue.put_nowait(_sse("batch_done", tally))
            except Exception as exc:  # backstop for a failure OUTSIDE the per-claim try
                logger.warning("ui_batch_failed batch_id=%s error=%s", batch_id, exc)
                queue.put_nowait(_sse("error", {"error": str(exc)}))
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(run())
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        completed = False
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    completed = True
                    break
                yield chunk
        finally:
            if completed:
                await task  # as before: surface a failure in the runner itself
            else:
                # The client went away (Cancel, or a closed tab). Signal and do NOT await: this runs
                # inside the generator's aclose(), and the in-flight claim can take ~40s.
                cancelled.set()

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/batches/{batch_id}/run-estimate")
async def run_estimate(batch_id: str):
    """What a "process lot" click is about to cost, measured rather than guessed.

    The claim count comes from the same query the run itself uses — deliberately not
    /api/dashboard's `not_investigated_count`, which excludes decided-but-never-investigated claims
    and would under-report the spend. The token figure is the median over the runs actually on disk;
    with no history it is None and the client says so, for the same reason there is no ETA.
    """
    if not queries.batch_exists(batch_id):
        return JSONResponse(status_code=404, content={"error": f"unknown batch: {batch_id!r}"})

    per_run = [tokens for tokens in
               (_run_tokens(_run_entry(run_dir).get("usage")) for run_dir in _all_run_dirs())
               if tokens is not None]
    return {
        "claims": len(queries.unresolved_claim_ids(batch_id, None)),
        "median_tokens_per_claim": int(median(per_run)) if per_run else None,
        "runs_measured": len(per_run),
    }


@app.post("/api/claims/{claim_id}/investigate")
async def investigate(claim_id: str):
    if not queries.claim_exists(claim_id):
        return JSONResponse(status_code=404, content={"error": f"unknown claim: {claim_id!r}"})
    try:
        result = await run_pipeline(claim_id=claim_id)
    except (PipelineError, AgentRunnerError) as exc:
        logger.warning("ui_investigate_failed claim_id=%s", claim_id)
        return JSONResponse(status_code=502, content={"error": str(exc)})
    return _result_payload(result)


@app.get("/api/claims/{claim_id}/stream")
async def stream(claim_id: str):
    """Single-claim drill-in: stream the two agents' tool calls, then the final verdict."""
    if not queries.claim_exists(claim_id):
        return JSONResponse(status_code=404, content={"error": f"unknown claim: {claim_id!r}"})

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        def make_hook(agent: str):
            def hook(record: ToolCallRecord) -> None:
                queue.put_nowait(_sse("tool_call", {
                    "agent": agent, "name": record.name,
                    "args": record.args, "is_error": record.is_error,
                }))
            return hook

        async def run() -> None:
            try:
                result = await run_pipeline(
                    claim_id=claim_id,
                    on_investigator_tool_call=make_hook("investigator"),
                    on_reviewer_tool_call=make_hook("reviewer"),
                )
                queue.put_nowait(_sse("done", _result_payload(result)))
            except Exception as exc:  # surface any failure in-band instead of crashing the stream
                logger.warning("ui_stream_failed claim_id=%s error=%s", claim_id, exc)
                queue.put_nowait(_sse("error", {"error": str(exc)}))
            finally:
                queue.put_nowait(None)

        task = asyncio.create_task(run())
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                yield chunk
        finally:
            await task

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# Mounted LAST so the explicit /api/* routes take precedence — FastAPI matches routes before mounts.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
