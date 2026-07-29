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
    fake = {"lot_total": 5, "todo_count": 4, "not_investigated_count": 4,
            "awaiting_my_call_count": 0, "decided_count": 1, "open_amount_cents": 30000,
            "oldest_open_days": 258, "priority_breakdown": {"HIGH": 2, "MEDIUM": 1, "LOW": 1},
            "batch": {"batch_id": "LOT-2024-09-15", "status": "complete"}}
    monkeypatch.setattr(queries, "dashboard_metrics", lambda: fake)
    assert client.get("/api/dashboard").json() == fake


def test_batch_returns_claims_and_forwards_every_query_param(monkeypatch):
    seen = {}

    def fake_batch_claims(batch_id, **kwargs):
        seen.update(batch_id=batch_id, **kwargs)
        return {"batch_id": batch_id, "total": 50, "offset": kwargs["offset"],
                "limit": kwargs["limit"], "claims": []}

    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "batch_claims", fake_batch_claims)
    resp = client.get(
        "/api/batches/LOT-2024-09-15?offset=25&limit=10&status_filter=disputable&sort=amount"
        "&direction=asc&q=walmart&retailer=kroger&reason=promo_billback"
        "&date_from=2024-09-01&date_to=2024-09-15")
    assert resp.status_code == 200 and resp.json()["total"] == 50
    # Asserted as a whole dict, not key by key: a param the route forgets to forward silently
    # narrows nothing, and the analyst sees an unfiltered page that looks perfectly plausible.
    assert seen == {"batch_id": "LOT-2024-09-15", "offset": 25, "limit": 10,
                    "status_filter": "disputable", "sort": "amount", "direction": "asc",
                    "q": "walmart", "retailer": "kroger", "reason": "promo_billback",
                    "date_from": "2024-09-01", "date_to": "2024-09-15"}


def test_unknown_batch_is_404(monkeypatch):
    monkeypatch.setattr(queries, "batch_exists", lambda b: False)
    assert client.get("/api/batches/nope").status_code == 404


def test_a_rejected_query_is_422_not_a_silent_fallback(monkeypatch):
    """The batch exists, the query doesn't. Distinct from the 404 above — and distinct from the
    pre-37a behaviour, which quietly served "all"/claim_id and said nothing."""
    def boom(batch_id, **kwargs):
        raise ValueError("unknown sort: 'nope' (expected one of age, amount, claim_id)")

    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "batch_claims", boom)
    resp = client.get("/api/batches/LOT-2024-09-15?sort=nope")
    assert resp.status_code == 422
    assert "unknown sort" in resp.json()["error"]


def test_filter_options_lists_the_lots_values(monkeypatch):
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "lot_filter_options",
                        lambda b: {"retailers": ["kroger", "walmart"], "reasons": ["shortage"]})
    resp = client.get("/api/batches/LOT-2024-09-15/filter-options")
    assert resp.status_code == 200
    assert resp.json() == {"retailers": ["kroger", "walmart"], "reasons": ["shortage"]}


def test_filter_options_unknown_batch_is_404(monkeypatch):
    monkeypatch.setattr(queries, "batch_exists", lambda b: False)
    assert client.get("/api/batches/nope/filter-options").status_code == 404


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

def _decidable(monkeypatch, agent_verdict="INVALID"):
    """A claim that exists and (by default) has an agent verdict to act on."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(queries, "agent_verdict", lambda c: agent_verdict)


def test_disposition_writes_and_returns(monkeypatch):
    _decidable(monkeypatch)
    seen = {}
    monkeypatch.setattr(server, "write_claim_disposition",
                        lambda **kw: seen.update(kw) or True)

    resp = client.post("/api/claims/CLM-002/disposition",
                       json={"disposition": "override", "override_verdict": "VALID", "note": "x"})
    assert resp.status_code == 200
    assert resp.json()["disposition"] == "override"
    # The body still carries override_verdict (the client's intent); the response reports the
    # decided_verdict actually stored.
    assert resp.json()["decided_verdict"] == "VALID"
    assert seen["claim_id"] == "CLM-002" and seen["override_verdict"] == "VALID"
    assert seen["note"] == "x" and "decided_at" in seen


def test_disposition_rejects_bad_value(monkeypatch):
    _decidable(monkeypatch)
    resp = client.post("/api/claims/CLM-002/disposition", json={"disposition": "maybe"})
    assert resp.status_code == 422  # pydantic Literal validation


def test_disposition_unknown_claim_is_404(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: False)
    resp = client.post("/api/claims/CLM-999/disposition", json={"disposition": "accept"})
    assert resp.status_code == 404


# --- Layer 34: you cannot sign off on something that isn't there ------------------------------------

def test_accept_on_an_uninvestigated_claim_is_409(monkeypatch):
    """Distinct from the 404 above: the claim exists, there is just no verdict to accept."""
    _decidable(monkeypatch, agent_verdict=None)
    wrote = []
    monkeypatch.setattr(server, "write_claim_disposition", lambda **kw: wrote.append(kw) or True)

    resp = client.post("/api/claims/CLM-002/disposition", json={"disposition": "accept"})
    assert resp.status_code == 409
    assert wrote == [], "must reject before writing"


def test_override_without_a_verdict_is_422(monkeypatch):
    _decidable(monkeypatch)
    resp = client.post("/api/claims/CLM-002/disposition",
                       json={"disposition": "override", "note": "x"})
    assert resp.status_code == 422


def test_override_without_a_note_is_422(monkeypatch):
    """An override is a human overruling an audited verdict — the one decision that most needs a
    stated reason. Whitespace doesn't count."""
    _decidable(monkeypatch)
    for note in (None, "", "   "):
        resp = client.post("/api/claims/CLM-002/disposition",
                           json={"disposition": "override", "override_verdict": "VALID", "note": note})
        assert resp.status_code == 422, f"note={note!r} should be rejected"


def test_override_to_the_agents_own_verdict_is_422(monkeypatch):
    _decidable(monkeypatch, agent_verdict="VALID")
    resp = client.post("/api/claims/CLM-002/disposition",
                       json={"disposition": "override", "override_verdict": "VALID", "note": "x"})
    assert resp.status_code == 422
    assert "accept it instead" in resp.json()["error"]


def test_override_on_an_uninvestigated_claim_is_allowed(monkeypatch):
    """Asymmetric with accept on purpose: source documents are served regardless of any agent run,
    so an analyst can rule on evidence the agents never saw."""
    _decidable(monkeypatch, agent_verdict=None)
    monkeypatch.setattr(server, "write_claim_disposition", lambda **kw: True)
    resp = client.post("/api/claims/CLM-002/disposition",
                       json={"disposition": "override", "override_verdict": "INVALID",
                             "note": "ASN proves the short ship"})
    assert resp.status_code == 200
    assert resp.json()["decided_verdict"] == "INVALID"


# --- bulk accept (Layer 38) -----------------------------------------------------------------------

def test_bulk_dispositions_returns_the_per_claim_outcome_map(monkeypatch):
    seen = {}
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(server, "write_claim_dispositions",
                        lambda **kw: seen.update(kw) or {"CLM-1": "recorded", "CLM-2": "recorded",
                                                         "CLM-3": "already_decided"})

    resp = client.post("/api/batches/LOT-2024-09-15/dispositions",
                       json={"claim_ids": ["CLM-1", "CLM-2", "CLM-3"]})

    assert resp.status_code == 200
    body = resp.json()
    assert body["results"] == {"CLM-1": "recorded", "CLM-2": "recorded", "CLM-3": "already_decided"}
    # `recorded` counts only what was actually written — the skipped claims are reported, not counted.
    assert body["recorded"] == 2
    assert body["batch_id"] == "LOT-2024-09-15" and body["decided_at"] == seen["decided_at"]
    # Batch-scoped: the writer must be told which lot, or the batch_id in the URL is decorative.
    assert seen["batch_id"] == "LOT-2024-09-15"
    assert seen["claim_ids"] == ["CLM-1", "CLM-2", "CLM-3"]


def test_bulk_dispositions_unknown_batch_is_404(monkeypatch):
    monkeypatch.setattr(queries, "batch_exists", lambda b: False)
    resp = client.post("/api/batches/LOT-NOPE/dispositions", json={"claim_ids": ["CLM-1"]})
    assert resp.status_code == 404


def test_bulk_dispositions_of_nothing_is_422(monkeypatch):
    """Distinct from a 200 with an empty map: an empty selection is a client bug, not a result."""
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    called = []
    monkeypatch.setattr(server, "write_claim_dispositions", lambda **kw: called.append(kw) or {})
    resp = client.post("/api/batches/LOT-2024-09-15/dispositions", json={"claim_ids": []})
    assert resp.status_code == 422
    assert called == [], "nothing should reach the writer"


def test_bulk_dispositions_refuses_a_smuggled_disposition(monkeypatch):
    """Accept-only is enforced by the absence of the field *plus* extra="forbid". Without the latter,
    a client posting {"disposition": "override"} would be silently bulk-*accepted* instead — told yes
    to a request the server never honoured."""
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    called = []
    monkeypatch.setattr(server, "write_claim_dispositions", lambda **kw: called.append(kw) or {})
    resp = client.post("/api/batches/LOT-2024-09-15/dispositions",
                       json={"claim_ids": ["CLM-1"], "disposition": "override",
                             "override_verdict": "VALID"})
    assert resp.status_code == 422
    assert called == []


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
