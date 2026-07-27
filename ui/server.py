"""FastAPI surface: the daily-lot dashboard + worklist over orchestrator.pipeline.run_pipeline.

Additive second entry point (the CLI is kept — see CLAUDE.md's "UI is additive"). Bound to
127.0.0.1 only, no auth, no rate limiting — same trust model as the CLI. Run with:

    uvicorn ui.server:app --host 127.0.0.1 --port 8000

Data comes from the relational store (ui/queries.py); "scenario" is retired (Layer 29/30).
"""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agents.base import AgentRunnerError, ToolCallRecord
from orchestrator.pipeline import PipelineError, PipelineResult, run_pipeline
from ui import queries

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Deduction Autopsy")


def _result_payload(result: PipelineResult) -> dict:
    """Final result shape shared by the single-claim `done` and the batch `claim_done` events."""
    return {
        "claim_id": result.claim_id,
        "investigator_verdict": result.investigator_verdict,
        "reviewer_verdict": result.reviewer_verdict,
        "final_verdict": result.final_verdict,
        "confidence": result.confidence,
        "dispute_grounds": result.reviewer_output.dispute_grounds,
        "usage": result.usage,
    }


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/dashboard")
async def dashboard():
    """Headline metrics for the active lot (unresolved / resolved-this-month / $ at risk / priority)."""
    return queries.dashboard_metrics()


@app.get("/api/batches/{batch_id}")
async def batch(batch_id: str, offset: int = 0, limit: int = 25):
    """One page of the lot's claims, each with derived priority + resolution status."""
    if not queries.batch_exists(batch_id):
        return JSONResponse(status_code=404, content={"error": f"unknown batch: {batch_id!r}"})
    return queries.batch_claims(batch_id, offset=offset, limit=limit)


@app.post("/api/batches/{batch_id}/investigate")
async def investigate_batch(batch_id: str, cap: int = 10):
    """Bulk-run the pipeline over the batch's unresolved claims (capped), streaming progress."""
    if not queries.batch_exists(batch_id):
        return JSONResponse(status_code=404, content={"error": f"unknown batch: {batch_id!r}"})

    claim_ids = queries.unresolved_claim_ids(batch_id, cap)

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        def make_hook(agent: str, claim_id: str):
            def hook(record: ToolCallRecord) -> None:
                queue.put_nowait(_sse("tool_call", {
                    "claim_id": claim_id, "agent": agent, "name": record.name,
                    "args": record.args, "is_error": record.is_error,
                }))
            return hook

        async def run() -> None:
            tally = {"investigated": 0, "VALID": 0, "INVALID": 0, "ESCALATE": 0}
            try:
                for claim_id in claim_ids:
                    result = await run_pipeline(
                        claim_id=claim_id,
                        on_investigator_tool_call=make_hook("investigator", claim_id),
                        on_reviewer_tool_call=make_hook("reviewer", claim_id),
                    )
                    tally["investigated"] += 1
                    tally[result.final_verdict] = tally.get(result.final_verdict, 0) + 1
                    queue.put_nowait(_sse("claim_done", _result_payload(result)))
                queue.put_nowait(_sse("batch_done", tally))
            except (PipelineError, AgentRunnerError) as exc:
                logger.warning("ui_batch_failed batch_id=%s", batch_id)
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
            except (PipelineError, AgentRunnerError) as exc:
                logger.warning("ui_stream_failed claim_id=%s", claim_id)
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
