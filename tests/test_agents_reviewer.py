import json

from fastmcp import Client

from agents.reviewer import REVIEWER_MODEL, run_reviewer
from mcp_server.server import mcp
from tests.agent_stubs import StubAsyncOpenAI, make_completion

SAMPLE_CASE_FILE = {
    "claim_id": "CLM-007b",
    "po_summary": {
        "ordered_qty_each": 120,
        "shipped_qty_each": 120,
        "received_qty_each": 120,
        "invoiced_qty_each": 120,
    },
    "timeline": [{"event": "order_date", "date": "2024-01-10", "valid": True}],
    "uom_conversions_applied": [],
    "prior_claims": ["CLM-007a"],
    "trade_agreement_found": False,
    "discrepancy_qty": 0,
    "discrepancy_amount_cents": 0,
    "proposed_verdict": "INVALID",
    "confidence": 0.9,
    "reasoning": "This narrative must never reach the Reviewer's prompt.",
}


async def test_default_model_is_confirmed_openrouter_slug():
    assert REVIEWER_MODEL == "anthropic/claude-sonnet-4.5"


async def test_reasoning_field_is_stripped_from_reviewer_prompt(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s07_duplicate_claim")
    stub = StubAsyncOpenAI([make_completion(content="{}")])

    async with Client(mcp) as mcp_client:
        await run_reviewer(openai_client=stub, mcp_client=mcp_client, case_file=SAMPLE_CASE_FILE)

    user_message = next(m for m in stub.requests[0]["messages"] if m["role"] == "user")
    assert "must never reach the Reviewer's prompt" not in user_message["content"]
    assert "CLM-007b" in user_message["content"]


async def test_case_file_is_xml_delimited(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s07_duplicate_claim")
    stub = StubAsyncOpenAI([make_completion(content="{}")])

    async with Client(mcp) as mcp_client:
        await run_reviewer(openai_client=stub, mcp_client=mcp_client, case_file=SAMPLE_CASE_FILE)

    user_message = next(m for m in stub.requests[0]["messages"] if m["role"] == "user")
    content = user_message["content"]
    start = content.index("<case_file>")
    end = content.index("</case_file>")
    assert start != -1 and end > start

    embedded = json.loads(content[start + len("<case_file>") : end].strip())
    assert embedded["claim_id"] == "CLM-007b"
    assert "reasoning" not in embedded


async def test_model_override_is_respected(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s07_duplicate_claim")
    stub = StubAsyncOpenAI([make_completion(content="{}")])

    async with Client(mcp) as mcp_client:
        await run_reviewer(
            openai_client=stub,
            mcp_client=mcp_client,
            case_file=SAMPLE_CASE_FILE,
            model="test-model",
        )

    assert stub.requests[0]["model"] == "test-model"


async def test_spot_check_tool_call_round_trip_against_real_fixtures(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s07_duplicate_claim")
    stub = StubAsyncOpenAI(
        [
            make_completion(
                tool_calls=[
                    {"id": "call_1", "name": "list_claims_for_po", "args": {"po_id": "PO-007"}}
                ]
            ),
            make_completion(content='{"final_verdict": "CONFIRM"}'),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_reviewer(
            openai_client=stub, mcp_client=mcp_client, case_file=SAMPLE_CASE_FILE
        )

    assert result.trace[0].name == "list_claims_for_po"
    assert result.trace[0].is_error is False
    claim_ids = json.loads(result.trace[0].result)
    assert "CLM-007a" in claim_ids and "CLM-007b" in claim_ids
    assert result.final_text == '{"final_verdict": "CONFIRM"}'
