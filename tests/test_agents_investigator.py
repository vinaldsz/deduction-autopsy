import json

from fastmcp import Client

from agents.investigator import INVESTIGATOR_MODEL, run_investigator
from mcp_server.server import mcp
from tests.agent_stubs import StubAsyncOpenAI, make_completion


async def test_default_model_is_confirmed_openrouter_slug():
    assert INVESTIGATOR_MODEL == "anthropic/claude-haiku-4.5"


async def test_user_message_references_claim_id(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI([make_completion(content="{}")])

    async with Client(mcp) as mcp_client:
        await run_investigator(openai_client=stub, mcp_client=mcp_client, claim_id="CLM-001")

    sent_messages = stub.requests[0]["messages"]
    assert sent_messages[0]["role"] == "system"
    user_message = next(m for m in sent_messages if m["role"] == "user")
    assert "CLM-001" in user_message["content"]


async def test_uses_default_model_unless_overridden(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI([make_completion(content="{}")])

    async with Client(mcp) as mcp_client:
        await run_investigator(openai_client=stub, mcp_client=mcp_client, claim_id="CLM-001")

    assert stub.requests[0]["model"] == "anthropic/claude-haiku-4.5"


async def test_model_override_is_respected(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI([make_completion(content="{}")])

    async with Client(mcp) as mcp_client:
        await run_investigator(
            openai_client=stub, mcp_client=mcp_client, claim_id="CLM-001", model="test-model"
        )

    assert stub.requests[0]["model"] == "test-model"


async def test_tool_call_round_trip_against_real_fixtures(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s02_casepack_mismatch")
    stub = StubAsyncOpenAI(
        [
            make_completion(
                tool_calls=[{"id": "call_1", "name": "get_po", "args": {"po_id": "PO-002"}}]
            ),
            make_completion(
                tool_calls=[
                    {
                        "id": "call_2",
                        "name": "normalize_uom",
                        "args": {"qty": 5, "from_uom": "CASE", "to_uom": "EACH", "sku": "SKU-002"},
                    }
                ]
            ),
            make_completion(content='{"proposed_verdict": "INVALID"}'),
        ]
    )

    async with Client(mcp) as mcp_client:
        result = await run_investigator(
            openai_client=stub, mcp_client=mcp_client, claim_id="CLM-002"
        )

    assert [record.name for record in result.trace] == ["get_po", "normalize_uom"]
    normalize_record = result.trace[1]
    assert normalize_record.is_error is False
    assert json.loads(normalize_record.result) == 120
    assert result.final_text == '{"proposed_verdict": "INVALID"}'
