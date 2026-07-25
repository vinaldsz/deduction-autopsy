"""FastAPI surface over orchestrator.pipeline.run_pipeline.

Additive second entry point (the CLI is kept — see CLAUDE.md's "UI is additive"). Bound to
127.0.0.1 only, no auth, no rate limiting — same trust model as the CLI. Run with:

    uvicorn ui.server:app --host 127.0.0.1 --port 8000
"""

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from agents.base import AgentRunnerError
from orchestrator.pipeline import PipelineError, PipelineResult, run_pipeline

logger = logging.getLogger(__name__)

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"

app = FastAPI(title="Deduction Autopsy")


def _scenario_exists(scenario: str) -> bool:
    return (SCENARIOS_DIR / scenario).is_dir()


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
        result = await run_pipeline(claim_id=claim_id, scenario=scenario)
    except (PipelineError, AgentRunnerError) as exc:
        logger.warning("ui_investigate_failed claim_id=%s scenario=%s", claim_id, scenario)
        return JSONResponse(status_code=502, content={"error": str(exc)})
    return _result_payload(result)
