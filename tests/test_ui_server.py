"""Layer 30b: dashboard/worklist API route + SSE shape tests.

Stubs `ui.queries` and `run_pipeline` so nothing hits the DB, OpenRouter, or the MCP subprocess —
these assert endpoint shapes, 404s, and SSE event order. The query SQL itself is covered by
tests/test_ui_queries.py; the pipeline by tests/test_orchestrator_pipeline.py.
"""

import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents.base import ToolCallRecord
from orchestrator.pipeline import CaseFile, PipelineError, ReviewerOutput
from ui import queries, server

client = TestClient(server.app)

_CASE_FILE = CaseFile.model_validate({
    "claim_id": "CLM-002",
    "po_summary": {"ordered_qty_each": 120, "shipped_qty_each": 120,
                   "received_qty_each": 120, "invoiced_qty_each": 96},
    "timeline": [{"event": "order_date", "date": "2024-02-01", "valid": True}],
    "proposed_verdict": "INVALID", "confidence": 0.97,
    "uom_conversions_applied": ["5 CASE -> 120 EACH for SKU-002 (factor 24)"],
    "discrepancy_qty": 24, "discrepancy_amount_cents": 12000,
})
_REVIEWER_OUTPUT = ReviewerOutput.model_validate({
    "claim_id": "CLM-002", "investigator_verdict": "INVALID",
    "review_findings": {"uom_check": "PASS"}, "final_verdict": "CONFIRM",
    "confidence": 0.97, "dispute_grounds": ["g"],
})


def _fake_result(claim_id="CLM-002", final="INVALID"):
    return SimpleNamespace(
        claim_id=claim_id, investigator_verdict="INVALID", reviewer_verdict="CONFIRM",
        final_verdict=final, confidence=0.97,
        case_file=_CASE_FILE, reviewer_output=_REVIEWER_OUTPUT,
        usage={"investigator": {"prompt_tokens": 1, "completion_tokens": 2},
               "reviewer": {"prompt_tokens": 3, "completion_tokens": 4}},
    )


def _sse_events(text):
    events = []
    for block in text.strip().split("\n\n"):
        if not block.strip():
            continue
        event = data = None
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data = json.loads(line[5:].strip())
        events.append((event, data))
    return events


# --- dashboard + batch (read) --------------------------------------------------------------------

def test_dashboard_returns_metrics(monkeypatch):
    fake = {"unresolved_count": 4, "resolved_this_month": 1, "dollars_at_risk_cents": 30000,
            "priority_breakdown": {"HIGH": 2, "MEDIUM": 1, "LOW": 1},
            "batch": {"batch_id": "LOT-2024-09-15", "status": "complete"}}
    monkeypatch.setattr(queries, "dashboard_metrics", lambda: fake)
    assert client.get("/api/dashboard").json() == fake


def test_batch_returns_claims_and_forwards_pagination(monkeypatch):
    seen = {}

    def fake_batch_claims(batch_id, offset=0, limit=25, status_filter="all", sort="claim_id", q=None):
        seen.update(batch_id=batch_id, offset=offset, limit=limit,
                    status_filter=status_filter, sort=sort, q=q)
        return {"batch_id": batch_id, "total": 50, "offset": offset, "limit": limit, "claims": []}

    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "batch_claims", fake_batch_claims)
    resp = client.get("/api/batches/LOT-2024-09-15?offset=25&limit=10&status_filter=escalated&sort=amount&q=walmart")
    assert resp.status_code == 200 and resp.json()["total"] == 50
    assert seen == {"batch_id": "LOT-2024-09-15", "offset": 25, "limit": 10,
                    "status_filter": "escalated", "sort": "amount", "q": "walmart"}


def test_unknown_batch_is_404(monkeypatch):
    monkeypatch.setattr(queries, "batch_exists", lambda b: False)
    assert client.get("/api/batches/nope").status_code == 404


# --- batch bulk-run SSE --------------------------------------------------------------------------

def test_batch_investigate_streams_per_claim_then_batch_done(monkeypatch):
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ["CLM-1", "CLM-2"])

    async def fake_pipeline(*, claim_id, on_investigator_tool_call=None, on_reviewer_tool_call=None, **kw):
        on_investigator_tool_call(ToolCallRecord(name="get_po", args={"po_id": claim_id}, result="{}"))
        on_reviewer_tool_call(ToolCallRecord(name="normalize_uom", args={}, result="1"))
        return _fake_result(claim_id, final="ESCALATE" if claim_id == "CLM-2" else "INVALID")

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)
    events = _sse_events(client.post("/api/batches/LOT-2024-09-15/investigate").text)
    names = [e for e, _ in events]
    assert names == ["tool_call", "tool_call", "claim_done",
                     "tool_call", "tool_call", "claim_done", "batch_done"]
    assert [d["claim_id"] for e, d in events if e == "claim_done"] == ["CLM-1", "CLM-2"]
    assert events[-1][1] == {"investigated": 2, "VALID": 0, "INVALID": 1, "ESCALATE": 1}


def test_batch_investigate_unknown_batch_is_404(monkeypatch):
    monkeypatch.setattr(queries, "batch_exists", lambda b: False)
    assert client.post("/api/batches/nope/investigate").status_code == 404


# --- single-claim drill-in -----------------------------------------------------------------------

def test_stream_emits_tool_calls_then_done(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)

    async def fake_pipeline(*, claim_id, on_investigator_tool_call=None, on_reviewer_tool_call=None, **kw):
        on_investigator_tool_call(ToolCallRecord(name="get_po", args={}, result="{}"))
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)
    events = _sse_events(client.get("/api/claims/CLM-002/stream").text)
    assert [e for e, _ in events] == ["tool_call", "done"]
    assert events[-1][1]["final_verdict"] == "INVALID"


def test_stream_unknown_claim_is_404(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: False)
    assert client.get("/api/claims/CLM-999/stream").status_code == 404


def test_done_payload_carries_case_file_evidence(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)

    async def fake_pipeline(*, claim_id, **kw):
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)
    done = _sse_events(client.get("/api/claims/CLM-002/stream").text)[-1][1]
    cf = done["case_file"]
    assert cf["po_summary"]["invoiced_qty_each"] == 96
    assert cf["discrepancy_amount_cents"] == 12000
    assert cf["uom_conversions_applied"] == ["5 CASE -> 120 EACH for SKU-002 (factor 24)"]
    assert cf["review_findings"]["uom_check"] == "PASS"


# --- source documents endpoint -------------------------------------------------------------------

def test_documents_returns_entity_graph(monkeypatch):
    fake = {"claim": {"claim_id": "CLM-002", "retailer_notes": "note"},
            "purchase_order": {"po_id": "PO-002"}, "asns": [], "invoices": [],
            "receiving_records": [], "trade_agreements": [], "prior_claims": []}
    monkeypatch.setattr(queries, "claim_documents", lambda c: fake)
    resp = client.get("/api/claims/CLM-002/documents")
    assert resp.status_code == 200 and resp.json()["purchase_order"]["po_id"] == "PO-002"


def test_documents_unknown_claim_is_404(monkeypatch):
    monkeypatch.setattr(queries, "claim_documents", lambda c: None)
    assert client.get("/api/claims/CLM-999/documents").status_code == 404


# --- casefile + dispute-packet read endpoints ----------------------------------------------------

def test_casefile_reads_latest_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    latest = tmp_path / "CLM-002" / "latest"
    latest.mkdir(parents=True)
    (latest / "case_file.json").write_text(json.dumps({"claim_id": "CLM-002", "case_file": {"x": 1}}))

    resp = client.get("/api/claims/CLM-002/casefile")
    assert resp.status_code == 200 and resp.json()["case_file"] == {"x": 1}


def test_casefile_404_when_not_investigated(monkeypatch, tmp_path):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    assert client.get("/api/claims/CLM-002/casefile").status_code == 404


def test_casefile_unknown_claim_is_404(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: False)
    assert client.get("/api/claims/CLM-999/casefile").status_code == 404


def test_dispute_packet_served_as_markdown_attachment(monkeypatch, tmp_path):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    latest = tmp_path / "CLM-002" / "latest"
    latest.mkdir(parents=True)
    (latest / "dispute_packet.md").write_text("# Dispute Packet — CLM-002\n")

    resp = client.get("/api/claims/CLM-002/dispute-packet")
    assert resp.status_code == 200
    assert "Dispute Packet" in resp.text
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "attachment" in resp.headers["content-disposition"]


def test_dispute_packet_404_when_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    assert client.get("/api/claims/CLM-002/dispute-packet").status_code == 404


def test_investigate_post_happy_and_errors(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)

    async def ok(*, claim_id, **kw):
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", ok)
    assert client.post("/api/claims/CLM-002/investigate").json()["final_verdict"] == "INVALID"

    async def boom(*, claim_id, **kw):
        raise PipelineError("nope")

    monkeypatch.setattr(server, "run_pipeline", boom)
    assert client.post("/api/claims/CLM-002/investigate").status_code == 502

    monkeypatch.setattr(queries, "claim_exists", lambda c: False)
    assert client.post("/api/claims/CLM-999/investigate").status_code == 404


# --- disposition (human decision) ----------------------------------------------------------------

def test_disposition_writes_and_returns(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    seen = {}
    monkeypatch.setattr(server, "write_claim_disposition",
                        lambda **kw: seen.update(kw) or True)

    resp = client.post("/api/claims/CLM-002/disposition",
                       json={"disposition": "override", "override_verdict": "VALID", "note": "x"})
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "override"
    assert seen["claim_id"] == "CLM-002" and seen["override_verdict"] == "VALID"
    assert seen["note"] == "x" and "decided_at" in seen


def test_disposition_rejects_bad_value(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    resp = client.post("/api/claims/CLM-002/disposition", json={"disposition": "maybe"})
    assert resp.status_code == 422  # pydantic Literal validation


def test_disposition_unknown_claim_is_404(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: False)
    resp = client.post("/api/claims/CLM-999/disposition", json={"disposition": "accept"})
    assert resp.status_code == 404


# --- static mount ---------------------------------------------------------------------------------

def test_index_served_and_api_not_shadowed(monkeypatch):
    root = client.get("/")
    assert root.status_code == 200 and "Deduction Autopsy" in root.text
    monkeypatch.setattr(queries, "dashboard_metrics", lambda: {"ok": True})
    assert client.get("/api/dashboard").json() == {"ok": True}  # /api/* still routes, not the mount


def test_lib_module_is_served():
    """app.js is `type=module` and imports ./lib.js, so a 404 here is a completely dead page — and
    test_index_served_and_api_not_shadowed above would still pass."""
    resp = client.get("/lib.js")
    assert resp.status_code == 200
    assert "export function dollars" in resp.text
