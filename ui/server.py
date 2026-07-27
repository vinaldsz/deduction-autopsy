"""FastAPI surface over orchestrator.pipeline.run_pipeline.

Additive second entry point (the CLI is kept — see CLAUDE.md's "UI is additive"). Bound to
127.0.0.1 only, no auth, no rate limiting — same trust model as the CLI. Run with:

    uvicorn ui.server:app --host 127.0.0.1 --port 8000
"""

import asyncio
import json
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agents.base import AgentRunnerError, ToolCallRecord
from orchestrator.ground_truth import GROUND_TRUTH
from orchestrator.pipeline import PipelineError, PipelineResult, run_pipeline

logger = logging.getLogger(__name__)

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Deduction Autopsy")


def _scenario_exists(scenario: str) -> bool:
    return (SCENARIOS_DIR / scenario).is_dir()


@app.get("/api/scenarios")
async def scenarios():
    """The picker data for the UI dropdown — scenario id + its fixed claim id.

    Sourced from GROUND_TRUTH (the same single source cli/run_all.py uses) so the list can't
    drift. Expected verdicts are deliberately omitted — the UI must not pre-empt the live result.
    """
    return {
        "scenarios": [
            {"scenario": g["scenario"], "claim_id": g["claim_id"]} for g in GROUND_TRUTH
        ]
    }


def _result_payload(result: PipelineResult) -> dict:
    """The final result shape shared by the sync endpoint's body and the SSE `done` event."""
    return {
        "claim_id": result.claim_id,
        "investigator_verdict": result.investigator_verdict,
        "reviewer_verdict": result.reviewer_verdict,
        "final_verdict": result.final_verdict,
        "confidence": result.confidence,
        "dispute_grounds": result.reviewer_output.dispute_grounds,
        "usage": result.usage,
    }


@app.post("/api/claims/{claim_id}/investigate")
async def investigate(claim_id: str, scenario: str):
    if not _scenario_exists(scenario):
        return JSONResponse(status_code=404, content={"error": f"unknown scenario: {scenario!r}"})
    try:
        result = await run_pipeline(claim_id=claim_id)
    except (PipelineError, AgentRunnerError) as exc:
        logger.warning("ui_investigate_failed claim_id=%s scenario=%s", claim_id, scenario)
        return JSONResponse(status_code=502, content={"error": str(exc)})
    return _result_payload(result)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@app.get("/api/claims/{claim_id}/stream")
async def stream(claim_id: str, scenario: str):
    if not _scenario_exists(scenario):
        return JSONResponse(status_code=404, content={"error": f"unknown scenario: {scenario!r}"})

    async def event_generator():
        queue: asyncio.Queue = asyncio.Queue()

        def make_hook(agent: str):
            def hook(record: ToolCallRecord) -> None:
                # The pipeline's tool-call hooks fire synchronously on this same event loop,
                # so put_nowait onto the unbounded queue is safe from here.
                queue.put_nowait(
                    _sse(
                        "tool_call",
                        {
                            "agent": agent,
                            "name": record.name,
                            "args": record.args,
                            "is_error": record.is_error,
                        },
                    )
                )

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
                logger.warning("ui_stream_failed claim_id=%s scenario=%s", claim_id, scenario)
                queue.put_nowait(_sse("error", {"error": str(exc)}))
            finally:
                queue.put_nowait(None)  # sentinel: no more events

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


# Mounted LAST so the explicit /api/* routes above take precedence — FastAPI matches routes
# before mounts. html=True serves index.html at "/". (Static mount deferred here from Layer 19.)
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
