import asyncio
import dataclasses
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from fastmcp.exceptions import ToolError
from openai import APIStatusError, APITimeoutError

from orchestrator.config import SETTINGS


@dataclass
class ToolCallRecord:
    name: str
    args: dict[str, Any]
    result: str
    is_error: bool = False


@dataclass
class AgentResult:
    final_text: str
    trace: list[ToolCallRecord] = field(default_factory=list)
    messages: list[dict] = field(default_factory=list)


class AgentRunnerError(RuntimeError):
    """Raised when the tool-use loop exceeds max_iterations without a final response."""


def _to_jsonable(value: Any) -> Any:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    return value


def _serialize_tool_result(data: Any) -> str:
    return json.dumps(_to_jsonable(data))


def _is_retryable_transport_error(exc: Exception) -> bool:
    if isinstance(exc, APITimeoutError):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code == 429 or exc.status_code >= 500
    return False


async def _build_tool_schemas(mcp_client: Any) -> list[dict]:
    mcp_tools = await mcp_client.list_tools()
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": tool.inputSchema,
            },
        }
        for tool in mcp_tools
    ]


class AgentRunner:
    """Shared Anthropic-style tool-use loop, driven over OpenRouter's OpenAI-compatible API."""

    def __init__(
        self,
        *,
        openai_client: Any,
        mcp_client: Any,
        model: str,
        system_prompt: str,
        temperature: float = SETTINGS.temperature,
        max_iterations: int = SETTINGS.max_tool_iterations,
        max_transport_attempts: int = SETTINGS.max_transport_attempts,
        retry_backoff_base_seconds: float = SETTINGS.retry_backoff_base_seconds,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_tool_call: Callable[[ToolCallRecord], None] | None = None,
    ) -> None:
        self._openai_client = openai_client
        self._mcp_client = mcp_client
        self._model = model
        self._system_prompt = system_prompt
        self._temperature = temperature
        self._max_iterations = max_iterations
        self._max_transport_attempts = max_transport_attempts
        self._retry_backoff_base_seconds = retry_backoff_base_seconds
        self._sleep = sleep
        self._on_tool_call = on_tool_call

    async def _create_completion(self, messages: list[dict], tools: list[dict]) -> Any:
        attempts_made = 0
        while True:
            attempts_made += 1
            try:
                return await self._openai_client.chat.completions.create(
                    model=self._model,
                    temperature=self._temperature,
                    messages=list(messages),
                    tools=tools,
                )
            except (APIStatusError, APITimeoutError) as exc:
                if not _is_retryable_transport_error(exc) or attempts_made >= self._max_transport_attempts:
                    raise
                backoff = self._retry_backoff_base_seconds * (2 ** (attempts_made - 1))
                await self._sleep(backoff)

    async def run(self, user_message: str) -> AgentResult:
        tools = await _build_tool_schemas(self._mcp_client)
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_message},
        ]
        trace: list[ToolCallRecord] = []

        for _ in range(self._max_iterations):
            response = await self._create_completion(messages, tools)
            msg = response.choices[0].message

            if not msg.tool_calls:
                messages.append({"role": "assistant", "content": msg.content})
                return AgentResult(final_text=msg.content or "", trace=trace, messages=messages)

            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError as exc:
                    args = {}
                    result_str = f"ERROR: invalid JSON arguments: {exc}"
                    is_error = True
                else:
                    try:
                        call_result = await self._mcp_client.call_tool(name, args)
                        result_str = _serialize_tool_result(call_result.data)
                        is_error = False
                    except ToolError as exc:
                        result_str = f"ERROR: {exc}"
                        is_error = True

                record = ToolCallRecord(name=name, args=args, result=result_str, is_error=is_error)
                trace.append(record)
                if self._on_tool_call is not None:
                    self._on_tool_call(record)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

        raise AgentRunnerError(
            f"exceeded max_iterations={self._max_iterations} without a final text response"
        )
