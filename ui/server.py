"""FastAPI surface: the daily-lot dashboard + worklist over orchestrator.pipeline.run_pipeline.

Additive second entry point (the CLI is kept — see CLAUDE.md's "UI is additive"). Bound to
127.0.0.1 only, no auth, no rate limiting — same trust model as the CLI. Run with:

    uvicorn ui.server:app --host 127.0.0.1 --port 8000

Data comes from the relational store (ui/queries.py); "scenario" is retired (Layer 29/30).
"""

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from agents.base import AgentRunnerError, ToolCallRecord
from orchestrator.dispositions import write_claim_disposition
from orchestrator.pipeline import PipelineError, PipelineResult, run_pipeline
from ui import queries

# Load .env so a locally-run `uvicorn ui.server:app` picks up OPENROUTER_API_KEY, matching the CLI.
load_dotenv()

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"

# Where the pipeline archives per-run artifacts (matches run_pipeline's default output_dir).
OUTPUT_DIR = Path("outputs")

app = FastAPI(title="Deduction Autopsy")


def _case_file_summary(result: PipelineResult) -> dict:
    """The evidence bundle the review workspace renders: reconciliation, timeline, checklist."""
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


@app.get("/api/dashboard")
async def dashboard():
    """Headline metrics for the active lot (unresolved / resolved-this-month / $ at risk / priority)."""
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
    """Record the analyst's decision on a claim (accept / override / send to human)."""
    if not queries.claim_exists(claim_id):
        return JSONResponse(status_code=404, content={"error": f"unknown claim: {claim_id!r}"})
    write_claim_disposition(
        claim_id=claim_id,
        disposition=body.disposition,
        override_verdict=body.override_verdict,
        note=body.note,
        decided_at=datetime.now(UTC).isoformat(),
    )
    return {"claim_id": claim_id, "disposition": body.disposition,
            "override_verdict": body.override_verdict}


@app.get("/api/batches/{batch_id}")
async def batch(
    batch_id: str,
    offset: int = 0,
    limit: int = 25,
    status_filter: str = "all",
    sort: str = "claim_id",
    q: str | None = None,
):
    """One page of the lot's claims — filtered/sorted/searched for the triage queue."""
    if not queries.batch_exists(batch_id):
        return JSONResponse(status_code=404, content={"error": f"unknown batch: {batch_id!r}"})
    return queries.batch_claims(
        batch_id, offset=offset, limit=limit, status_filter=status_filter, sort=sort, q=q
    )


@app.post("/api/batches/{batch_id}/investigate")
async def investigate_batch(batch_id: str, cap: int | None = None):
    """Process the whole lot: run the pipeline over every unresolved claim, streaming progress.
    `cap` optionally limits it (defaults to the entire lot — the ingestion "process lot" step)."""
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
            except Exception as exc:  # surface any failure in-band instead of crashing the stream
                logger.warning("ui_batch_failed batch_id=%s error=%s", batch_id, exc)
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
