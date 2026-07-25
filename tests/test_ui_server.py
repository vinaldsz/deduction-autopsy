"""Smoke tests for the FastAPI UI (Layer 19).

Stubs `run_pipeline` so nothing hits OpenRouter or spawns the real MCP subprocess — the tests
exercise route shapes and status codes only. Layer 22 expands this file.
"""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import ui.server as server
from agents.base import AgentRunnerError
from orchestrator.pipeline import PipelineError

client = TestClient(server.app)


def _fake_result(claim_id="CLM-002"):
    """A stand-in for PipelineResult carrying just the fields _result_payload reads."""
    return SimpleNamespace(
        claim_id=claim_id,
        investigator_verdict="INVALID",
        reviewer_verdict="CONFIRM",
        final_verdict="INVALID",
        confidence=0.97,
        reviewer_output=SimpleNamespace(
            dispute_grounds=["Normalized quantities match: 5 CASE = 120 EACH"]
        ),
        usage={
            "investigator": {"prompt_tokens": 10, "completion_tokens": 2},
            "reviewer": {"prompt_tokens": 20, "completion_tokens": 3},
        },
    )


def test_investigate_returns_result_shape(monkeypatch):
    async def fake_pipeline(*, claim_id, scenario, **kwargs):
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)

    resp = client.post("/api/claims/CLM-002/investigate?scenario=s02_casepack_mismatch")

    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "claim_id": "CLM-002",
        "investigator_verdict": "INVALID",
        "reviewer_verdict": "CONFIRM",
        "final_verdict": "INVALID",
        "confidence": 0.97,
        "dispute_grounds": ["Normalized quantities match: 5 CASE = 120 EACH"],
        "usage": {
            "investigator": {"prompt_tokens": 10, "completion_tokens": 2},
            "reviewer": {"prompt_tokens": 20, "completion_tokens": 3},
        },
    }


def test_investigate_unknown_scenario_is_404(monkeypatch):
    called = False

    async def fake_pipeline(*, claim_id, scenario, **kwargs):
        nonlocal called
        called = True
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)

    resp = client.post("/api/claims/CLM-002/investigate?scenario=s99_nope")

    assert resp.status_code == 404
    assert "error" in resp.json()
    assert called is False  # rejected before the pipeline ran


@pytest.mark.parametrize("exc", [PipelineError("boom"), AgentRunnerError("upstream 500")])
def test_investigate_pipeline_failure_is_502(monkeypatch, exc):
    async def fake_pipeline(*, claim_id, scenario, **kwargs):
        raise exc

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)

    resp = client.post("/api/claims/CLM-002/investigate?scenario=s02_casepack_mismatch")

    assert resp.status_code == 502
    assert resp.json() == {"error": str(exc)}
