from types import SimpleNamespace

import pytest

from k8s_servant.llm import build_chat
from k8s_servant.llm.chat_completions import ChatCompletionsChat
from k8s_servant.llm.types import ModelOutputTruncated, ToolCall, ToolSpec, Turn

SPEC = ToolSpec("t", "a tool", {"type": "object", "properties": {}})


class _FakeCompletions:
    """Records the kwargs of chat.completions.create and returns a canned reply."""

    def __init__(self, message=None, finish_reason="stop", usage=None):
        self.kwargs = None
        self.message = message or SimpleNamespace(content="reply", tool_calls=None)
        self.finish_reason = finish_reason
        self.usage = usage

    async def create(self, **kwargs):
        self.kwargs = kwargs
        choice = SimpleNamespace(message=self.message, finish_reason=self.finish_reason)
        return SimpleNamespace(choices=[choice], usage=self.usage)


def _chat(completions, effort=""):
    return ChatCompletionsChat(SimpleNamespace(chat=SimpleNamespace(completions=completions)), "my-model", effort)


async def test_sends_tools_only_when_present():
    completions = _FakeCompletions()
    chat = _chat(completions)
    reply = await chat.complete([Turn("user", "hi")], [])
    assert reply == Turn("assistant", "reply")
    assert completions.kwargs == {"model": "my-model", "messages": [{"role": "user", "content": "hi"}]}
    await chat.complete([], [SPEC])
    assert completions.kwargs["tools"] == [
        {"type": "function", "function": {"name": "t", "description": "a tool", "parameters": SPEC.parameters}}
    ]


async def test_use_tools_false_omits_tools():  # how this API forbids tool calls
    completions = _FakeCompletions()
    await _chat(completions).complete([Turn("user", "q")], [SPEC], use_tools=False)
    assert "tools" not in completions.kwargs


async def test_sends_reasoning_effort_when_set():
    completions = _FakeCompletions()
    await _chat(completions, "medium").complete([], [])
    assert completions.kwargs["reasoning_effort"] == "medium"


async def test_turns_convert_to_chat_messages():
    completions = _FakeCompletions()
    turns = [
        Turn("system", "S"),
        Turn("user", "Q"),
        Turn("assistant", "", [ToolCall("c1", "t", "{}")]),
        Turn("tool", "out", tool_call_id="c1"),
    ]
    await _chat(completions).complete(turns, [])
    assert completions.kwargs["messages"] == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "Q"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "out"},
    ]


async def test_reply_tool_calls_become_turn_tool_calls():
    call = SimpleNamespace(id="c9", function=SimpleNamespace(name="t", arguments='{"a": 1}'))
    completions = _FakeCompletions(SimpleNamespace(content=None, tool_calls=[call]))
    reply = await _chat(completions).complete([Turn("user", "q")], [SPEC])
    assert reply == Turn("assistant", "", [ToolCall("c9", "t", '{"a": 1}')])


async def test_length_finish_reason_raises_truncated():
    chat = _chat(_FakeCompletions(finish_reason="length"))
    with pytest.raises(ModelOutputTruncated):
        await chat.complete([Turn("user", "q")], [])


async def test_logs_and_counts_tokens(caplog):
    # Needed to size the deployment: the first deep health check hit the 50k
    # tokens-per-minute limit and there was no way to see how many it used.
    usage = SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=300,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1024),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=200),
    )
    chat = _chat(_FakeCompletions(usage=usage))
    caplog.set_level("INFO")
    await chat.complete([], [])
    await chat.complete([], [])
    # Cached prompt tokens are billed at a discount; they show the real cost.
    assert "1200 prompt (1024 cached) + 300 completion tokens (200 reasoning)" in caplog.text
    assert chat.total_tokens == 3000


def test_build_chat_uses_the_configured_deployment_and_effort():
    config = {
        "foundry_endpoint": "https://x.openai.azure.com",
        "foundry_api_key": "k",
        "foundry_timeout_seconds": 5,
        "foundry_deployment": "dep",
        "foundry_reasoning_effort": "low",
    }
    chat = build_chat(config)
    assert (chat.model, chat.reasoning_effort) == ("dep", "low")
