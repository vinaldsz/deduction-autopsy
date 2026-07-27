"""Smoke tests for the FastAPI UI (Layers 19-20).

Stubs `run_pipeline` so nothing hits OpenRouter or spawns the real MCP subprocess — the tests
exercise route shapes, status codes, and SSE framing only. Layer 22 expands this file.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import ui.server as server
from agents.base import AgentRunnerError, ToolCallRecord
from orchestrator.ground_truth import GROUND_TRUTH
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


# --- Layer 19: POST /investigate ------------------------------------------------


def test_investigate_returns_result_shape(monkeypatch):
    async def fake_pipeline(*, claim_id, **kwargs):
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

    async def fake_pipeline(*, claim_id, **kwargs):
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
    async def fake_pipeline(*, claim_id, **kwargs):
        raise exc

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)

    resp = client.post("/api/claims/CLM-002/investigate?scenario=s02_casepack_mismatch")

    assert resp.status_code == 502
    assert resp.json() == {"error": str(exc)}


# --- Layer 20: GET /stream (SSE) ------------------------------------------------


def _sse_events(text):
    """Parse raw SSE text into a list of (event, data-line) pairs."""
    events = []
    for block in text.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(l[len("event: ") :] for l in lines if l.startswith("event: "))
        data = next(l[len("data: ") :] for l in lines if l.startswith("data: "))
        events.append((event, data))
    return events


def test_stream_emits_tool_calls_then_done(monkeypatch):
    async def fake_pipeline(
        *, claim_id, on_investigator_tool_call=None, on_reviewer_tool_call=None, **kwargs
    ):
        on_investigator_tool_call(ToolCallRecord(name="get_po", args={"po_id": "PO-002"}, result="{}"))
        on_reviewer_tool_call(ToolCallRecord(name="normalize_uom", args={}, result="{}"))
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)

    resp = client.get("/api/claims/CLM-002/stream?scenario=s02_casepack_mismatch")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _sse_events(resp.text)
    assert [e for e, _ in events] == ["tool_call", "tool_call", "done"]

    first = json.loads(events[0][1])
    assert first == {
        "agent": "investigator",
        "name": "get_po",
        "args": {"po_id": "PO-002"},
        "is_error": False,
    }
    second = json.loads(events[1][1])
    assert second["agent"] == "reviewer" and second["name"] == "normalize_uom"
    done = json.loads(events[2][1])
    assert done["claim_id"] == "CLM-002" and done["final_verdict"] == "INVALID"


def test_stream_unknown_scenario_is_404(monkeypatch):
    async def fake_pipeline(*, claim_id, **kwargs):
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)

    resp = client.get("/api/claims/CLM-002/stream?scenario=s99_nope")

    assert resp.status_code == 404
    assert "error" in resp.json()


def test_stream_pipeline_failure_emits_error_event(monkeypatch):
    async def fake_pipeline(*, claim_id, **kwargs):
        raise PipelineError("boom")

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)

    resp = client.get("/api/claims/CLM-002/stream?scenario=s02_casepack_mismatch")

    assert resp.status_code == 200  # stream opens, failure surfaces as an in-band event
    events = _sse_events(resp.text)
    assert [e for e, _ in events] == ["error"]
    assert json.loads(events[0][1]) == {"error": "boom"}


# --- Layer 22: /api/scenarios + static frontend ---------------------------------


def test_scenarios_endpoint_lists_all_ground_truth():
    resp = client.get("/api/scenarios")

    assert resp.status_code == 200
    scenarios = resp.json()["scenarios"]
    # One entry per ground-truth row, in the same order, with exactly scenario + claim_id.
    assert scenarios == [
        {"scenario": g["scenario"], "claim_id": g["claim_id"]} for g in GROUND_TRUTH
    ]
    # Expected verdicts must not leak to the UI (it must not pre-empt the live result).
    assert all(set(s) == {"scenario", "claim_id"} for s in scenarios)


def test_index_html_is_served_at_root():
    resp = client.get("/")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "Deduction Autopsy" in resp.text  # marker from index.html


def test_static_mount_does_not_shadow_api_routes(monkeypatch):
    # The "/" mount is registered last; explicit /api/* routes must still win over it.
    monkeypatch.setattr(server, "run_pipeline", lambda **kw: _fake_result(kw["claim_id"]))

    scenarios_resp = client.get("/api/scenarios")
    assert scenarios_resp.headers["content-type"].startswith("application/json")
    assert "scenarios" in scenarios_resp.json()

    async def fake_pipeline(*, claim_id, **kwargs):
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)
    invest_resp = client.post("/api/claims/CLM-002/investigate?scenario=s02_casepack_mismatch")
    assert invest_resp.headers["content-type"].startswith("application/json")
    assert invest_resp.json()["claim_id"] == "CLM-002"
