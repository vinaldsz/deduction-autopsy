"""Layer 30b: dashboard/worklist API route + SSE shape tests.

Stubs `ui.queries` and `run_pipeline` so nothing hits the DB, OpenRouter, or the MCP subprocess —
these assert endpoint shapes, 404s, and SSE event order. The query SQL itself is covered by
tests/test_ui_queries.py; the pipeline by tests/test_orchestrator_pipeline.py.
"""

import asyncio
import json
import os
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
    # `batch_start` leads: the progress counter needs a denominator, and only the server knows the
    # worklist. `failed`/`stopped_reason` joined the tally with the per-claim try (Layer 40).
    assert names == ["batch_start", "claim_start", "tool_call", "tool_call", "claim_done",
                     "claim_start", "tool_call", "tool_call", "claim_done", "batch_done"]
    assert events[0][1] == {"total": 2}
    assert [d["claim_id"] for e, d in events if e == "claim_done"] == ["CLM-1", "CLM-2"]
    assert events[-1][1] == {"investigated": 2, "VALID": 0, "INVALID": 1, "ESCALATE": 1,
                             "failed": 0, "stopped_reason": None}


def test_batch_investigate_unknown_batch_is_404(monkeypatch):
    monkeypatch.setattr(queries, "batch_exists", lambda b: False)
    assert client.post("/api/batches/nope/investigate").status_code == 404


# --- Layer 40: a batch stream that survives one claim failing -------------------------------------

def _failing_pipeline(monkeypatch, fails: set[str], seen: list[str]):
    """Stub `run_pipeline` that raises for the named claims and records every claim it was asked
    for. The call list is what proves a claim was never *started*, which no frame can show."""
    async def fake_pipeline(*, claim_id, **kw):
        seen.append(claim_id)
        if claim_id in fails:
            raise PipelineError(f"boom on {claim_id}")
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", fake_pipeline)


def test_one_failing_claim_does_not_end_the_lot(monkeypatch):
    """The defect this layer exists for: the `try` used to wrap the whole loop, so claim 2 of 3
    raising abandoned claim 3 and threw away the tally for claim 1."""
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ["CLM-1", "CLM-2", "CLM-3"])
    seen: list[str] = []
    _failing_pipeline(monkeypatch, {"CLM-2"}, seen)

    events = _sse_events(client.post("/api/batches/LOT-2024-09-15/investigate").text)
    assert [e for e, _ in events] == ["batch_start",
                                      "claim_start", "claim_done",
                                      "claim_start", "claim_error",
                                      "claim_start", "claim_done", "batch_done"]
    assert seen == ["CLM-1", "CLM-2", "CLM-3"]
    error = next(d for e, d in events if e == "claim_error")
    assert error["claim_id"] == "CLM-2" and "boom on CLM-2" in error["error"]
    assert events[-1][1] == {"investigated": 2, "VALID": 0, "INVALID": 2, "ESCALATE": 0,
                             "failed": 1, "stopped_reason": None}


def test_every_claim_announces_itself_before_it_can_fail(monkeypatch):
    """`claim_start` precedes the attempt, so a claim that dies before its first tool call has still
    named itself. The progress counter used to infer the current claim from `tool_call`; a claim that
    failed at the first LLM call therefore never announced itself, and the line went on naming the
    PREVIOUS claim while the counter advanced past it — seen live, on a real run."""
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ["CLM-1", "CLM-2", "CLM-3"])
    _failing_pipeline(monkeypatch, {"CLM-2"}, [])

    events = _sse_events(client.post("/api/batches/LOT-2024-09-15/investigate").text)
    assert [d["claim_id"] for e, d in events if e == "claim_start"] == ["CLM-1", "CLM-2", "CLM-3"]
    # The failing claim announced itself even though it emitted no tool_call at all.
    order = [e for e, _ in events]
    assert order.index("claim_start") < order.index("claim_error")


def test_three_consecutive_failures_stop_the_lot(monkeypatch):
    """Per-claim recovery alone turns a dead API key into one paid failure per claim. The call
    list, not the frame count, is what shows claims 4 and 5 were never attempted."""
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    ids = ["CLM-1", "CLM-2", "CLM-3", "CLM-4", "CLM-5"]
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ids)
    seen: list[str] = []
    _failing_pipeline(monkeypatch, set(ids), seen)

    events = _sse_events(client.post("/api/batches/LOT-2024-09-15/investigate").text)
    assert [e for e, _ in events] == ["batch_start",
                                      "claim_start", "claim_error",
                                      "claim_start", "claim_error",
                                      "claim_start", "claim_error", "batch_done"]
    assert seen == ["CLM-1", "CLM-2", "CLM-3"]
    assert events[-1][1] == {"investigated": 0, "VALID": 0, "INVALID": 0, "ESCALATE": 0,
                             "failed": 3, "stopped_reason": "consecutive_failures"}


def test_a_success_resets_the_consecutive_failure_count(monkeypatch):
    """Scattered failures across a long lot are not a systemic fault. Two, a success, then three
    more: the breaker must fire on the trailing run, not on the total of five."""
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    ids = ["CLM-1", "CLM-2", "CLM-3", "CLM-4", "CLM-5", "CLM-6", "CLM-7"]
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ids)
    seen: list[str] = []
    _failing_pipeline(monkeypatch, {"CLM-1", "CLM-2", "CLM-4", "CLM-5", "CLM-6"}, seen)

    events = _sse_events(client.post("/api/batches/LOT-2024-09-15/investigate").text)
    assert seen == ["CLM-1", "CLM-2", "CLM-3", "CLM-4", "CLM-5", "CLM-6"]  # CLM-7 never started
    assert events[-1][1]["failed"] == 5
    assert events[-1][1]["investigated"] == 1
    assert events[-1][1]["stopped_reason"] == "consecutive_failures"


async def test_cancelling_the_stream_stops_the_lot_at_the_next_claim(monkeypatch):
    """Cancel is a real stop, not a client that looks away while the server keeps spending.

    Driven against the generator directly rather than through TestClient, which consumes a
    streaming response whole and so can never disconnect mid-stream. CLM-2 is *gated* rather than
    merely slow: the event queue is unbounded, so a stub that returns instantly lets the producer
    finish the entire lot before the consumer reads its first frame, and there would be nothing
    left to cancel. Parking CLM-2 reproduces the only state a real cancel ever happens in — one
    claim in flight, the rest untouched.
    """
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ["CLM-1", "CLM-2", "CLM-3"])
    seen: list[str] = []
    release = asyncio.Event()

    async def gated_pipeline(*, claim_id, **kw):
        seen.append(claim_id)
        if claim_id != "CLM-1":
            await release.wait()
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", gated_pipeline)

    before = set(server._background_tasks)
    response = await server.investigate_batch("LOT-2024-09-15")
    body = response.body_iterator
    frames = []
    async for chunk in body:
        frames.append(chunk)
        if "claim_done" in chunk:
            break
    task = next(iter(set(server._background_tasks) - before))

    await body.aclose()
    assert seen == ["CLM-1", "CLM-2"], "CLM-2 was in flight when the client disconnected"

    release.set()  # the in-flight claim is allowed to finish and be persisted
    await asyncio.wait_for(task, timeout=1)
    assert seen == ["CLM-1", "CLM-2"], f"the lot kept running after cancel: {seen}"
    assert any("batch_start" in f for f in frames)


async def test_cancelling_does_not_block_on_the_in_flight_claim(monkeypatch):
    """`finally: await task` would have made aclose() wait out the running claim — ~40s of held
    response teardown for a click whose whole purpose is to stop waiting."""
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ["CLM-1", "CLM-2"])
    started = asyncio.Event()

    async def slow_pipeline(*, claim_id, **kw):
        started.set()
        await asyncio.sleep(5)
        return _fake_result(claim_id)

    monkeypatch.setattr(server, "run_pipeline", slow_pipeline)
    response = await server.investigate_batch("LOT-2024-09-15")
    body = response.body_iterator
    await body.__anext__()          # batch_start
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.wait_for(body.aclose(), timeout=1)


# --- Layer 40: the spend estimate behind the run confirm ------------------------------------------

def _write_run(tmp_path, claim_id, run_id, usage):
    run_dir = tmp_path / claim_id / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "verdict.json").write_text(json.dumps({"final_verdict": "VALID", "usage": usage}))


def test_run_estimate_reports_the_median_of_real_past_runs(monkeypatch, tmp_path):
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ["CLM-1", "CLM-2"])
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    for run_id, total in (("r1", 100), ("r2", 300), ("r3", 200)):
        _write_run(tmp_path, "CLM-9", run_id, {
            "investigator": {"prompt_tokens": total // 2, "completion_tokens": 0},
            "reviewer": {"prompt_tokens": total // 2, "completion_tokens": 0},
        })
    assert client.get("/api/batches/LOT-2024-09-15/run-estimate").json() == {
        "claims": 2, "median_tokens_per_claim": 200, "runs_measured": 3}


def test_run_estimate_says_so_rather_than_guessing_with_no_history(monkeypatch, tmp_path):
    """The same rule as the absent ETA: no measurement is reported as no measurement."""
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: ["CLM-1"])
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path / "gone")
    assert client.get("/api/batches/LOT-2024-09-15/run-estimate").json() == {
        "claims": 1, "median_tokens_per_claim": None, "runs_measured": 0}


def test_run_estimate_skips_runs_that_recorded_no_usage(monkeypatch, tmp_path):
    """17 of the 57 real run dirs predate the current layout. A run with no usage is not a run
    that cost zero, so counting it as 0 would drag the median toward a number nothing measured."""
    monkeypatch.setattr(queries, "batch_exists", lambda b: True)
    monkeypatch.setattr(queries, "unresolved_claim_ids", lambda b, cap: [])
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    _write_run(tmp_path, "CLM-9", "r1", None)
    _write_run(tmp_path, "CLM-9", "r2", {"investigator": {"prompt_tokens": 40,
                                                          "completion_tokens": 10}})
    assert client.get("/api/batches/LOT-2024-09-15/run-estimate").json() == {
        "claims": 0, "median_tokens_per_claim": 50, "runs_measured": 1}


def test_run_estimate_unknown_batch_is_404(monkeypatch):
    monkeypatch.setattr(queries, "batch_exists", lambda b: False)
    assert client.get("/api/batches/nope/run-estimate").status_code == 404


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


# --- run history + stored trace (Layer 39) -------------------------------------------------------
#
# These build run directories the way orchestrator/output.py really does, which the older fixtures
# above deliberately do not: they `mkdir` a directory named `latest`, while `prepare_run_dir` makes it
# a relative *symlink* to a run id. Both shapes are exercised, because the mkdir one is what an
# artifact written before the run-dir layout looks like.

def _run(tmp_path, claim, run_id, *, timestamp, final="INVALID", case_file=True, packet=True):
    """One archived run, with the verdict.json the pipeline always writes."""
    run_dir = tmp_path / claim / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "verdict.json").write_text(
        json.dumps(
            {
                "claim_id": claim, "investigator_verdict": final, "reviewer_verdict": "CONFIRM",
                "final_verdict": final, "confidence": 0.98, "timestamp": timestamp,
                "usage": {"investigator": {"prompt_tokens": 12536, "completion_tokens": 1653},
                          "reviewer": {"prompt_tokens": 14515, "completion_tokens": 1613}},
            }
        )
    )
    if case_file:
        (run_dir / "case_file.json").write_text(json.dumps({"claim_id": claim}))
    if packet:
        (run_dir / "dispute_packet.md").write_text("# Dispute Packet\n")
    return run_dir


def _link_latest(tmp_path, claim, run_id):
    """`latest` as orchestrator/output.py makes it: a relative symlink, not a directory."""
    os.symlink(run_id, tmp_path / claim / "latest", target_is_directory=True)


def test_done_payload_carries_both_agent_reasonings(monkeypatch):
    """Both strings were populated, persisted and served by /casefile — and dropped from the live
    payload, so a claim explained itself after a reload but not after the run that produced it."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    result = _fake_result()
    # Set here rather than on the shared _CASE_FILE: both fields default to "", so asserting against
    # the default would pass whether the server forwarded them or not.
    result.case_file = _CASE_FILE.model_copy(update={"reasoning": "Casepack, not a shortage."})
    result.reviewer_output = _REVIEWER_OUTPUT.model_copy(
        update={"reasoning": "Recomputed the conversion; the Investigator's factor holds."})

    async def ok(*, claim_id, **kw):
        return result

    monkeypatch.setattr(server, "run_pipeline", ok)

    case_file = client.post("/api/claims/CLM-002/investigate").json()["case_file"]
    assert case_file["investigator_reasoning"] == "Casepack, not a shortage."
    assert case_file["reviewer_reasoning"].startswith("Recomputed the conversion")


def test_runs_lists_newest_first_skipping_the_latest_symlink(monkeypatch, tmp_path):
    """`latest` is a real symlink to a run dir, and `is_dir()` follows it — so an enumeration that
    doesn't exclude it by name reports the newest run twice, once under the name "latest"."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    _run(tmp_path, "CLM-002", "20260725T062603Z", timestamp="2026-07-25T06:26:56+00:00")
    _run(tmp_path, "CLM-002", "20260728T062104Z", timestamp="2026-07-28T06:21:50+00:00")
    _link_latest(tmp_path, "CLM-002", "20260728T062104Z")

    body = client.get("/api/claims/CLM-002/runs").json()
    assert [r["run_id"] for r in body["runs"]] == ["20260728T062104Z", "20260725T062603Z"]
    assert body["latest_run_id"] == "20260728T062104Z"


def test_runs_orders_by_verdict_timestamp_not_directory_name(monkeypatch, tmp_path):
    """Run ids are caller-supplied strings: outputs/CLM-002 really holds `run-A`/`run-B` beside four
    timestamp-shaped ids, and they sort lexically *last* while being the oldest runs on disk."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    _run(tmp_path, "CLM-002", "20260728T062104Z", timestamp="2026-07-28T06:21:50+00:00")
    _run(tmp_path, "CLM-002", "run-B", timestamp="2026-07-24T02:33:45+00:00")

    body = client.get("/api/claims/CLM-002/runs").json()
    assert [r["run_id"] for r in body["runs"]] == ["20260728T062104Z", "run-B"]


def test_runs_ignores_loose_files_beside_run_dirs(monkeypatch, tmp_path):
    """outputs/CLM-008 has verdict.json / reasoning_trace.json / dispute_packet.md sitting at the claim
    level from before the run-dir layout. A file is not a run."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    _run(tmp_path, "CLM-008", "20260727T012615Z", timestamp="2026-07-27T01:26:15+00:00")
    (tmp_path / "CLM-008" / "verdict.json").write_text("{}")
    (tmp_path / "CLM-008" / "dispute_packet.md").write_text("# stray\n")

    body = client.get("/api/claims/CLM-008/runs").json()
    assert [r["run_id"] for r in body["runs"]] == ["20260727T012615Z"]


def test_runs_reports_a_run_that_cannot_be_rebuilt(monkeypatch, tmp_path):
    """17 of the real run dirs predate Layer 32 and have no case_file.json, and only an INVALID verdict
    gets a dispute packet — so being listed does not mean being openable."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    _run(tmp_path, "CLM-002", "20260725T062603Z", timestamp="2026-07-25T06:26:56+00:00",
         final="VALID", case_file=False, packet=False)

    (run,) = client.get("/api/claims/CLM-002/runs").json()["runs"]
    assert run["has_case_file"] is False and run["has_dispute_packet"] is False
    assert run["final_verdict"] == "VALID" and run["usage"]["investigator"]["prompt_tokens"] == 12536


def test_runs_tolerates_a_run_with_no_readable_verdict(monkeypatch, tmp_path):
    """prepare_run_dir mkdirs before the pipeline can fail, so a crashed run leaves a bare directory.
    It sorts last — having no timestamp — rather than ahead of runs that finished."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    _run(tmp_path, "CLM-002", "20260728T062104Z", timestamp="2026-07-28T06:21:50+00:00")
    (tmp_path / "CLM-002" / "20260729T000000Z").mkdir()
    (tmp_path / "CLM-002" / "20260730T000000Z").mkdir()
    (tmp_path / "CLM-002" / "20260730T000000Z" / "verdict.json").write_text("{ not json")

    body = client.get("/api/claims/CLM-002/runs").json()
    assert body["runs"][0]["run_id"] == "20260728T062104Z"
    assert {r["run_id"] for r in body["runs"][1:]} == {"20260729T000000Z", "20260730T000000Z"}
    assert body["runs"][1]["final_verdict"] is None


def test_runs_is_empty_for_a_claim_nobody_has_investigated(monkeypatch, tmp_path):
    """200 with an empty list, not 404: the client asks this for every claim it opens, and "never
    investigated" is a true answer rather than a missing resource."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)

    resp = client.get("/api/claims/CLM-004/runs")
    assert resp.status_code == 200
    assert resp.json() == {"claim_id": "CLM-004", "latest_run_id": None, "runs": []}


def test_runs_latest_run_id_is_none_when_latest_is_not_a_symlink(monkeypatch, tmp_path):
    """Guards os.readlink against the pre-run-dir shape (and against the mkdir-based fixtures above).
    Path.resolve().name would have answered the literal "latest" as though it were a run id."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    (tmp_path / "CLM-002" / "latest").mkdir(parents=True)
    (tmp_path / "CLM-002" / "latest" / "verdict.json").write_text("{}")

    body = client.get("/api/claims/CLM-002/runs").json()
    assert body["latest_run_id"] is None
    assert body["runs"] == []


def test_runs_unknown_claim_is_404(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: False)
    assert client.get("/api/claims/CLM-999/runs").status_code == 404


def test_trace_compacts_the_stored_tool_calls(monkeypatch, tmp_path):
    """The persisted trace holds raw OpenAI messages with both system prompts and `arguments` as a JSON
    *string*; the client renders `{agent, name, args, is_error}` with args as a dict."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    latest = tmp_path / "CLM-002" / "latest"
    latest.mkdir(parents=True)
    (latest / "reasoning_trace.json").write_text(json.dumps({
        "claim_id": "CLM-002",
        "investigator": [
            {"role": "system", "content": "You are the Investigator..."},
            {"role": "assistant", "content": "I'll investigate.", "tool_calls": [
                {"id": "call-1", "type": "function",
                 "function": {"name": "get_deduction_claim",
                              "arguments": '{"claim_id": "CLM-002"}'}}]},
            {"role": "tool", "tool_call_id": "call-1", "content": '{"po_id": "PO-002"}'},
        ],
        "reviewer": [
            {"role": "assistant", "content": None, "tool_calls": [
                {"id": "call-2", "type": "function",
                 "function": {"name": "normalize_uom", "arguments": '{"sku": "SKU-002"}'}}]},
        ],
    }))

    body = client.get("/api/claims/CLM-002/trace").json()
    assert body["tool_calls"] == [
        {"agent": "investigator", "name": "get_deduction_claim",
         "args": {"claim_id": "CLM-002"}, "is_error": False},
        {"agent": "reviewer", "name": "normalize_uom", "args": {"sku": "SKU-002"}, "is_error": False},
    ]


def test_trace_marks_an_errored_call_from_the_result_that_followed_it(monkeypatch, tmp_path):
    """`is_error` is not stored: agents/base.py writes an "ERROR: " prefix into the tool result, so the
    call is marked from the reply matching its tool_call_id — read back, not recomputed."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    latest = tmp_path / "CLM-002" / "latest"
    latest.mkdir(parents=True)
    (latest / "reasoning_trace.json").write_text(json.dumps({
        "investigator": [
            {"role": "assistant", "tool_calls": [
                {"id": "bad", "function": {"name": "get_po", "arguments": '{"po_id": "CLM-002"}'}},
                {"id": "good", "function": {"name": "get_po", "arguments": '{"po_id": "PO-002"}'}}]},
            {"role": "tool", "tool_call_id": "bad", "content": "ERROR: unknown po: 'CLM-002'"},
            {"role": "tool", "tool_call_id": "good", "content": '{"po_id": "PO-002"}'},
        ],
    }))

    calls = client.get("/api/claims/CLM-002/trace").json()["tool_calls"]
    assert [c["is_error"] for c in calls] == [True, False]


def test_trace_survives_unparseable_arguments(monkeypatch, tmp_path):
    """Mirrors agents/base.py, which records `args = {}` and an error when the model emits bad JSON —
    the trace must render rather than 500 on the very call that went wrong."""
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    latest = tmp_path / "CLM-002" / "latest"
    latest.mkdir(parents=True)
    (latest / "reasoning_trace.json").write_text(json.dumps({
        "investigator": [{"role": "assistant", "tool_calls": [
            {"id": "x", "function": {"name": "get_po", "arguments": "{not json"}}]}],
    }))

    (call,) = client.get("/api/claims/CLM-002/trace").json()["tool_calls"]
    assert call["args"] == {} and call["name"] == "get_po"


def test_trace_404_when_the_run_stored_none(monkeypatch, tmp_path):
    monkeypatch.setattr(queries, "claim_exists", lambda c: True)
    monkeypatch.setattr(server, "OUTPUT_DIR", tmp_path)
    assert client.get("/api/claims/CLM-002/trace").status_code == 404


def test_trace_unknown_claim_is_404(monkeypatch):
    monkeypatch.setattr(queries, "claim_exists", lambda c: False)
    assert client.get("/api/claims/CLM-999/trace").status_code == 404


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


def test_every_frontend_module_is_served():
    """index.html loads only /app.js and the rest arrive through the import graph, so a single 404
    anywhere in it is a blank page with one console error. Globbed rather than listed: the point is to
    cover a module nobody remembered to add here."""
    modules = sorted(p.name for p in (server.STATIC_DIR).glob("*.js"))
    assert len(modules) > 10, f"expected the split frontend, found {modules}"
    for name in modules:
        resp = client.get(f"/{name}")
        assert resp.status_code == 200, f"/{name} is not served"
        assert resp.headers["content-type"].startswith("text/javascript"), name
