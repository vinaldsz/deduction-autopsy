import json
from types import SimpleNamespace

import httpx
from openai import APIStatusError, APITimeoutError
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.chat.chat_completion_message_tool_call import (
    ChatCompletionMessageToolCall,
    ChatCompletionMessageToolCallUnion,
    Function,
)
from openai.types.completion_usage import CompletionUsage


def make_completion(content=None, tool_calls=None, usage=None):
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
    parsed_usage = None
    if usage is not None:
        prompt_tokens, completion_tokens = usage
        parsed_usage = CompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
    return ChatCompletion(
        id="chatcmpl-test",
        choices=[choice],
        created=0,
        model="test-model",
        object="chat.completion",
        usage=parsed_usage,
    )


def floor_tool_calls(claim_id: str, *, omit: str | None = None):
    """One scripted assistant turn whose batched tool calls satisfy the Layer-31 completeness gate.

    Since Layer 31 the pipeline requires a full minimum investigation of *every* claim
    (orchestrator/completeness.py), so a stubbed `run_pipeline` script has to contain those calls or
    the gate triggers a correction retry and the scripted queue runs dry. Generated from
    `required_tool_calls` itself rather than a hardcoded list, so it cannot drift from the gate.

    One completion, not six: `make_completion` takes a list of tool calls and `agents/base.py`
    executes them all in a single loop iteration — which is also how the real models behave (live
    traces show 6-8 calls across 3-4 turns).

    Pass `omit="normalize_uom"` to deliberately leave one requirement unsatisfied, for tests that
    exercise the correction-retry path.
    """
    from mcp_server.fixtures import FixtureLoader
    from orchestrator.completeness import required_tool_calls

    requirements = required_tool_calls(claim_id)
    if not requirements:
        raise ValueError(f"{claim_id!r} is not in the store, so it has no requirements to satisfy")

    loader = FixtureLoader()
    claim = loader.get_claim(claim_id)
    assert claim is not None  # guaranteed by the requirements check above
    po = loader.get_po(claim.po_id)

    calls = []
    for index, requirement in enumerate(requirements, start=1):
        if requirement.tool == omit:
            continue
        args = dict(requirement.args)
        if requirement.tool == "normalize_uom" and po is not None:
            # from_uom == to_uom is an identity no-op in the real tool, so this satisfies the
            # requirement regardless of which document supplied the differing unit.
            args = {
                "qty": po.ordered_qty,
                "from_uom": po.ordered_uom,
                "to_uom": "EACH",
                "sku": po.sku,
            }
        elif requirement.tool == "get_trade_agreement" and po is not None:
            # A non-matching promo_code returns None, which is not an error, so the requirement is
            # met by having consulted the tool at all.
            args = {"retailer": claim.retailer, "sku": po.sku, "promo_code": "PROMO-STUB"}
        calls.append({"id": f"floor_{index}", "name": requirement.tool, "args": args})
    return make_completion(tool_calls=calls)


class StubAsyncOpenAI:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.requests.append(kwargs)
        result = next(self._responses)
        if isinstance(result, BaseException):
            raise result
        return result


def make_status_error(status_code: int) -> APIStatusError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx.Response(status_code=status_code, request=request)
    return APIStatusError(f"error {status_code}", response=response, body=None)


def make_timeout_error() -> APITimeoutError:
    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    return APITimeoutError(request=request)
