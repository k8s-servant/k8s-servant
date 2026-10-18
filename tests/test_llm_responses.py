from types import SimpleNamespace

import pytest
from openai.types.responses import Response

from k8s_servant.llm.responses import ResponsesChat
from k8s_servant.llm.types import ModelCallFailed, ModelOutputTruncated, ToolCall, ToolSpec, Turn

SPEC = ToolSpec("t", "d", {"type": "object"})

REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "ENC"}
CALL = {
    "type": "function_call",
    "id": "fc_1",
    "call_id": "call_1",
    "name": "t",
    "arguments": "{}",
    "status": "completed",
}
MESSAGE = {
    "type": "message",
    "id": "msg_1",
    "role": "assistant",
    "status": "completed",
    "content": [{"type": "output_text", "text": "hi", "annotations": []}],
}


def _response(output, status="completed", usage=None, **extra):
    """A real SDK Response, so conversion is tested against the real shapes."""
    return Response.model_validate(
        {
            "id": "resp_1",
            "created_at": 0,
            "model": "m",
            "object": "response",
            "parallel_tool_calls": True,
            "tool_choice": "auto",
            "tools": [],
            "status": status,
            "output": output,
            "usage": usage,
            **extra,
        }
    )


class _FakeResponses:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def _chat(*replies, effort=""):
    fake = _FakeResponses(*replies)
    chat = ResponsesChat(SimpleNamespace(responses=fake), "my-model", effort)
    return chat, fake


async def test_request_is_stateless_and_carries_encrypted_reasoning():
    chat, fake = _chat(_response([MESSAGE]))
    reply = await chat.complete([Turn("system", "S"), Turn("user", "q")], [])
    kw = fake.calls[0]
    assert kw["model"] == "my-model"
    assert kw["input"] == [{"role": "system", "content": "S"}, {"role": "user", "content": "q"}]
    assert kw["store"] is False and kw["include"] == ["reasoning.encrypted_content"]
    assert "temperature" not in kw and "tools" not in kw and "tool_choice" not in kw
    assert reply.role == "assistant" and reply.content == "hi" and reply.tool_calls == []


async def test_tools_are_sent_non_strict():
    chat, fake = _chat(_response([MESSAGE]))
    await chat.complete([Turn("user", "q")], [SPEC])
    kw = fake.calls[0]
    assert kw["tools"] == [
        {"type": "function", "name": "t", "description": "d", "parameters": {"type": "object"}, "strict": False}
    ]
    assert "tool_choice" not in kw


async def test_reasoning_items_are_sent_back_on_the_next_call():
    chat, fake = _chat(_response([REASONING, CALL]), _response([MESSAGE]))
    first = await chat.complete([Turn("user", "q")], [SPEC])
    assert first.tool_calls == [ToolCall("call_1", "t", "{}")]
    await chat.complete([Turn("user", "q"), first, Turn("tool", "out", tool_call_id="call_1")], [SPEC])
    second_input = fake.calls[1]["input"]
    assert any(i.get("type") == "reasoning" and i.get("encrypted_content") == "ENC" for i in second_input)
    assert any(i.get("type") == "function_call" and i.get("call_id") == "call_1" for i in second_input)
    assert second_input[-1] == {"type": "function_call_output", "call_id": "call_1", "output": "out"}


async def test_stored_items_are_valid_input_items():
    # Nothing the input types do not know (and no None values) may come back.
    chat, _ = _chat(_response([REASONING, CALL, MESSAGE]))
    reply = await chat.complete([Turn("user", "q")], [SPEC])
    assert all(None not in i.values() for i in reply.provider_state)
    assert [i["type"] for i in reply.provider_state] == ["reasoning", "function_call", "message"]


async def test_use_tools_false_sends_tool_choice_none_and_keeps_definitions():
    chat, fake = _chat(_response([MESSAGE]))
    await chat.complete([Turn("user", "q")], [SPEC], use_tools=False)
    assert fake.calls[0]["tool_choice"] == "none" and len(fake.calls[0]["tools"]) == 1
    await chat.complete([Turn("user", "q")], [], use_tools=False)
    assert "tool_choice" not in fake.calls[1]


async def test_assistant_turn_without_text_sends_no_empty_message():
    chat, fake = _chat(_response([MESSAGE]))
    turns = [Turn("assistant", "", [ToolCall("c1", "t", "{}")]), Turn("tool", "o", tool_call_id="c1")]
    await chat.complete(turns, [])
    assert fake.calls[0]["input"] == [
        {"type": "function_call", "call_id": "c1", "name": "t", "arguments": "{}"},
        {"type": "function_call_output", "call_id": "c1", "output": "o"},
    ]
    await chat.complete([Turn("assistant", "text")], [])
    assert fake.calls[1]["input"] == [{"role": "assistant", "content": "text"}]


async def test_incomplete_raises_truncated():
    chat, _ = _chat(_response([], status="incomplete"))
    with pytest.raises(ModelOutputTruncated):
        await chat.complete([Turn("user", "q")], [])


async def test_effort_omitted_when_empty():
    chat, fake = _chat(_response([MESSAGE]))
    await chat.complete([], [])
    assert "reasoning" not in fake.calls[0]
    chat, fake = _chat(_response([MESSAGE]), effort="high")
    await chat.complete([], [])
    assert fake.calls[0]["reasoning"] == {"effort": "high"}


async def test_usage_is_logged_and_counted(caplog):
    usage = {
        "input_tokens": 1200,
        "input_tokens_details": {"cached_tokens": 1024, "cache_write_tokens": 0},
        "output_tokens": 300,
        "output_tokens_details": {"reasoning_tokens": 200},
        "total_tokens": 1500,
    }
    chat, _ = _chat(_response([MESSAGE], usage=usage))
    caplog.set_level("INFO")
    await chat.complete([], [])
    await chat.complete([], [])
    assert "1200 prompt (1024 cached) + 300 completion tokens (200 reasoning)" in caplog.text
    assert chat.total_tokens == 3000


async def test_failed_response_raises_with_the_provider_error():
    error = {"code": "server_error", "message": "boom"}
    chat, _ = _chat(_response([], status="failed", error=error))
    with pytest.raises(ModelCallFailed, match="the model call failed: server_error: boom"):
        await chat.complete([Turn("user", "q")], [])


async def test_cancelled_response_raises():
    chat, _ = _chat(_response([], status="cancelled"))
    with pytest.raises(ModelCallFailed):
        await chat.complete([Turn("user", "q")], [])


async def test_incomplete_for_content_filter_is_a_failure_not_truncation():
    chat, _ = _chat(_response([], status="incomplete", incomplete_details={"reason": "content_filter"}))
    with pytest.raises(ModelCallFailed, match="content filter") as info:
        await chat.complete([Turn("user", "q")], [])
    assert not isinstance(info.value, ModelOutputTruncated)
    chat, _ = _chat(_response([], status="incomplete", incomplete_details={"reason": "max_output_tokens"}))
    with pytest.raises(ModelOutputTruncated):
        await chat.complete([Turn("user", "q")], [])


async def test_reasoning_only_reply_gets_a_placeholder_message_on_the_next_call():
    # Output with only a reasoning item would leave the next request without the
    # assistant's answer; a placeholder message keeps it valid.
    chat, fake = _chat(_response([REASONING]), _response([MESSAGE]))
    first = await chat.complete([Turn("user", "q")], [])
    await chat.complete([Turn("user", "q"), first, Turn("user", "again")], [])
    items = fake.calls[1]["input"]
    assert items[1:3] == [REASONING, {"role": "assistant", "content": "(no answer)"}]
    assert items[3] == {"role": "user", "content": "again"}


async def test_reply_with_a_message_or_call_gets_no_placeholder():
    chat, fake = _chat(_response([REASONING, CALL]), _response([MESSAGE]))
    first = await chat.complete([Turn("user", "q")], [SPEC])
    await chat.complete([Turn("user", "q"), first, Turn("tool", "out", tool_call_id="call_1")], [SPEC])
    assert {"role": "assistant", "content": "(no answer)"} not in fake.calls[1]["input"]


async def test_empty_assistant_turn_sends_a_placeholder_message():
    chat, fake = _chat(_response([MESSAGE]))
    await chat.complete([Turn("user", "q"), Turn("assistant", ""), Turn("user", "again")], [])
    assert fake.calls[0]["input"][1] == {"role": "assistant", "content": "(no answer)"}
