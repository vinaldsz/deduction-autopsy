import dataclasses
import json
from dataclasses import dataclass, field
from typing import Any

from fastmcp.exceptions import ToolError


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
        temperature: float = 0.0,
        max_iterations: int = 10,
    ) -> None:
        self._openai_client = openai_client
        self._mcp_client = mcp_client
        self._model = model
        self._system_prompt = system_prompt
        self._temperature = temperature
        self._max_iterations = max_iterations

    async def run(self, user_message: str) -> AgentResult:
        tools = await _build_tool_schemas(self._mcp_client)
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_message},
        ]
        trace: list[ToolCallRecord] = []

        for _ in range(self._max_iterations):
            response = await self._openai_client.chat.completions.create(
                model=self._model,
                temperature=self._temperature,
                messages=list(messages),
                tools=tools,
            )
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

                trace.append(ToolCallRecord(name=name, args=args, result=result_str, is_error=is_error))
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})

        raise AgentRunnerError(
            f"exceeded max_iterations={self._max_iterations} without a final text response"
        )
