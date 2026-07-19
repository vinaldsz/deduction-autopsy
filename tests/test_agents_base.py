import json
from types import SimpleNamespace

import pytest
from fastmcp import Client
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    Function,
    ChatCompletionMessageToolCall,
    ChatCompletionMessageToolCallUnion,
)

from agents.base import AgentRunner, AgentRunnerError
from mcp_server.server import mcp


def make_completion(content=None, tool_calls=None):
    parsed_tool_calls: list[ChatCompletionMessageToolCallUnion] | None = (
        [
            ChatCompletionMessageToolCall(
                id=tc["id"],
                type="function",
                function=Function(
                    name=tc["name"],
                    arguments=tc["raw_arguments"] if "raw_arguments" in tc else json.dumps(tc["args"]),
                ),
            )
            for tc in tool_calls
        ]
        if tool_calls
        else None
    )
    message = ChatCompletionMessage(
        role="assistant",
        content=content,
        tool_calls=parsed_tool_calls,
    )
    choice = Choice(
        index=0,
        message=message,
        finish_reason="tool_calls" if tool_calls else "stop",
    )
    return ChatCompletion(
        id="chatcmpl-test",
        choices=[choice],
        created=0,
        model="test-model",
        object="chat.completion",
    )


class StubAsyncOpenAI:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        return next(self._responses)


async def test_text_only_response_no_tools():
    stub = StubAsyncOpenAI([make_completion(content="No discrepancy found.")])

    async with Client(mcp) as mcp_client:
        runner = AgentRunner(
            openai_client=stub,
            mcp_client=mcp_client,
            model="test-model",
            system_prompt="You are a test agent.",
        )
        result = await runner.run("Investigate claim CLM-001.")

    assert result.final_text == "No discrepancy found."
    assert result.trace == []
    assert len(stub.requests) == 1


async def test_single_tool_call_round_trip(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI(
        [
            make_completion(tool_calls=[{"id": "call_1", "name": "get_po", "args": {"po_id": "PO-001"}}]),
            make_completion(content="Done."),
        ]
    )

    async with Client(mcp) as mcp_client:
        runner = AgentRunner(
            openai_client=stub,
            mcp_client=mcp_client,
            model="test-model",
            system_prompt="You are a test agent.",
        )
        result = await runner.run("Look up PO-001.")

    assert result.final_text == "Done."
    assert len(result.trace) == 1
    record = result.trace[0]
    assert record.name == "get_po"
    assert record.args == {"po_id": "PO-001"}
    assert record.is_error is False
    assert json.loads(record.result)["po_id"] == "PO-001"

    second_request_messages = stub.requests[1]["messages"]
    assert second_request_messages[-2]["role"] == "assistant"
    assert second_request_messages[-2]["tool_calls"][0]["function"]["name"] == "get_po"
    assert second_request_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": record.result,
    }


async def test_parallel_tool_calls_in_one_turn(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    stub = StubAsyncOpenAI(
        [
            make_completion(
                tool_calls=[
                    {"id": "call_1", "name": "get_po", "args": {"po_id": "PO-001"}},
                    {"id": "call_2", "name": "get_invoice", "args": {"po_id": "PO-001"}},
                ]
            ),
            make_completion(content="Done."),
        ]
    )

    async with Client(mcp) as mcp_client:
        runner = AgentRunner(
            openai_client=stub,
            mcp_client=mcp_client,
            model="test-model",
            system_prompt="You are a test agent.",
        )
        result = await runner.run("Look up PO-001 and its invoice.")

    assert [record.name for record in result.trace] == ["get_po", "get_invoice"]
    tool_messages = [m for m in stub.requests[1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in tool_messages] == ["call_1", "call_2"]


async def test_tool_error_surfaces_without_crashing(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s02_casepack_mismatch")
    stub = StubAsyncOpenAI(
        [
            make_completion(
                tool_calls=[
                    {
                        "id": "call_1",
                        "name": "normalize_uom",
                        "args": {"qty": 5, "from_uom": "CASE", "to_uom": "EACH", "sku": "UNKNOWN-SKU"},
                    }
                ]
            ),
            make_completion(content="Could not normalize UOM."),
        ]
    )

    async with Client(mcp) as mcp_client:
        runner = AgentRunner(
            openai_client=stub,
            mcp_client=mcp_client,
            model="test-model",
            system_prompt="You are a test agent.",
        )
        result = await runner.run("Normalize the UOM.")

    assert result.final_text == "Could not normalize UOM."
    assert len(result.trace) == 1
    assert result.trace[0].is_error is True
    assert result.trace[0].result.startswith("ERROR:")


async def test_malformed_tool_call_json_handled():
    stub = StubAsyncOpenAI(
        [
            make_completion(
                tool_calls=[{"id": "call_1", "name": "get_po", "raw_arguments": "{not valid json"}],
            ),
            make_completion(content="Recovered."),
        ]
    )

    async with Client(mcp) as mcp_client:
        runner = AgentRunner(
            openai_client=stub,
            mcp_client=mcp_client,
            model="test-model",
            system_prompt="You are a test agent.",
        )
        result = await runner.run("Trigger malformed args.")

    assert result.final_text == "Recovered."
    assert result.trace[0].is_error is True
    assert result.trace[0].result.startswith("ERROR:")


async def test_max_iterations_raises(monkeypatch):
    monkeypatch.setenv("SCENARIO_ID", "s01_clean_shortage")
    always_calls_tool = [
        make_completion(tool_calls=[{"id": f"call_{i}", "name": "get_po", "args": {"po_id": "PO-001"}}])
        for i in range(20)
    ]
    stub = StubAsyncOpenAI(always_calls_tool)

    async with Client(mcp) as mcp_client:
        runner = AgentRunner(
            openai_client=stub,
            mcp_client=mcp_client,
            model="test-model",
            system_prompt="You are a test agent.",
            max_iterations=3,
        )
        with pytest.raises(AgentRunnerError):
            await runner.run("Never finish.")

    assert len(stub.requests) == 3


async def test_mcp_tool_schema_translation():
    from agents.base import _build_tool_schemas

    async with Client(mcp) as mcp_client:
        schemas = await _build_tool_schemas(mcp_client)

    assert len(schemas) == 8
    for schema in schemas:
        assert schema["type"] == "function"
        assert schema["function"]["name"]
        assert schema["function"]["description"], f"{schema['function']['name']} has no description"
        assert "parameters" in schema["function"]

    get_po_schema = next(s for s in schemas if s["function"]["name"] == "get_po")
    assert get_po_schema["function"]["parameters"]["required"] == ["po_id"]
    assert get_po_schema["function"]["parameters"]["properties"]["po_id"]["type"] == "string"
