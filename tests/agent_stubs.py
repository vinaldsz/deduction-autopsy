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
