from types import SimpleNamespace

import pytest
from anthropic import AsyncAnthropic, AsyncAnthropicFoundry
from anthropic.types import Message, MessageParam
from pydantic import TypeAdapter

from k8s_servant.llm.auth import ModelAuth
from k8s_servant.llm.messages import MessagesChat, build_messages_client
from k8s_servant.llm.types import ModelCallFailed, ModelOutputTruncated, ToolCall, ToolSpec, Turn

SPEC = ToolSpec("t", "d", {"type": "object"})
THINKING = {"type": "thinking", "thinking": "hmm", "signature": "SIG"}
TEXT = {"type": "text", "text": "hi"}
TOOL_USE = {"type": "tool_use", "id": "tu_1", "name": "t", "input": {"a": 1}}


def _message(content, stop_reason="end_turn", usage=None):
    """A real SDK Message, so conversion is tested against the real shapes."""
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "m",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": usage or {"input_tokens": 1, "output_tokens": 1},
        }
    )


class _FakeMessages:
    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


def _chat(*replies, effort=""):
    fake = _FakeMessages(*replies)
    chat = MessagesChat(SimpleNamespace(messages=fake), "my-model", effort, 4000)
    return chat, fake


async def test_text_reply():
    chat, _ = _chat(_message([TEXT]))
    out = await chat.complete([Turn("user", "q")], [])
    assert out.role == "assistant" and out.content == "hi" and out.tool_calls == []


async def test_tool_use_becomes_tool_calls_with_json_arguments():
    chat, _ = _chat(_message([TEXT, TOOL_USE], stop_reason="tool_use"))
    out = await chat.complete([Turn("user", "q")], [SPEC])
    assert out.tool_calls == [ToolCall("tu_1", "t", '{"a": 1}')]


async def test_parallel_tool_results_become_one_user_message():
    turns = [
        Turn("user", "q"),
        Turn("assistant", "", [ToolCall("a", "t", "{}"), ToolCall("b", "t", "{}")]),
        Turn("tool", "A", tool_call_id="a"),
        Turn("tool", "B", tool_call_id="b"),
    ]
    chat, fake = _chat(_message([TEXT]))
    await chat.complete(turns, [SPEC])
    kw = fake.calls[0]
    assert kw["messages"][-1] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "A"},
            {"type": "tool_result", "tool_use_id": "b", "content": "B"},
        ],
    }
    assert len(kw["messages"]) == 3


async def test_no_empty_text_block_for_tool_only_turn():
    turns = [Turn("user", "q"), Turn("assistant", "", [ToolCall("a", "t", '{"x": 1}')])]
    chat, fake = _chat(_message([TEXT]))
    await chat.complete(turns, [SPEC])
    assert fake.calls[0]["messages"][1] == {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": "a", "name": "t", "input": {"x": 1}}],
    }


async def test_thinking_blocks_are_sent_back_with_signatures():
    chat, fake = _chat(_message([THINKING, TEXT, TOOL_USE], stop_reason="tool_use"), _message([TEXT]))
    first = await chat.complete([Turn("user", "q")], [SPEC])
    assert first.provider_state[:2] == [THINKING, TEXT]
    assert first.provider_state[2]["type"] == "tool_use" and first.provider_state[2]["input"] == {"a": 1}
    turns = [Turn("user", "q"), first, Turn("tool", "R", tool_call_id="tu_1")]
    await chat.complete(turns, [SPEC])
    sent = fake.calls[1]["messages"]
    assert sent[1] == {"role": "assistant", "content": first.provider_state}
    assert sent[1]["content"][0] == THINKING
    # The stored blocks must be valid request input for the SDK's own types.
    TypeAdapter(list[MessageParam]).validate_python(sent)


async def test_system_is_top_level_and_caching_is_on():
    turns = [Turn("system", "S"), Turn("system", "T"), Turn("user", "q")]
    chat, fake = _chat(_message([TEXT]))
    await chat.complete(turns, [])
    kw = fake.calls[0]
    assert kw["system"] == "S\n\nT" and kw["cache_control"] == {"type": "ephemeral"}
    assert all(m["role"] != "system" for m in kw["messages"]) and "temperature" not in kw
    assert kw["max_tokens"] == 4000 and kw["model"] == "my-model"
    assert "tools" not in kw and "tool_choice" not in kw


@pytest.mark.parametrize(
    "effort,thinking,output_config",
    [
        ("", None, None),
        ("none", {"type": "disabled"}, None),
        ("minimal", {"type": "adaptive"}, {"effort": "low"}),
        ("low", {"type": "adaptive"}, {"effort": "low"}),
        ("medium", {"type": "adaptive"}, {"effort": "medium"}),
        ("max", {"type": "adaptive"}, {"effort": "max"}),
    ],
)
async def test_effort_mapping(effort, thinking, output_config):
    chat, fake = _chat(_message([TEXT]), effort=effort)
    await chat.complete([Turn("user", "q")], [])
    kw = fake.calls[0]
    assert kw.get("thinking") == thinking
    assert kw.get("output_config") == output_config


async def test_tools_are_sent_in_the_messages_format():
    chat, fake = _chat(_message([TEXT]))
    await chat.complete([Turn("user", "q")], [SPEC])
    kw = fake.calls[0]
    assert kw["tools"] == [{"name": "t", "description": "d", "input_schema": {"type": "object"}}]
    assert kw["tool_choice"] == {"type": "auto"}


async def test_use_tools_false_keeps_tools_with_choice_none():
    chat, fake = _chat(_message([TEXT]))
    await chat.complete([Turn("user", "q")], [SPEC], use_tools=False)
    kw = fake.calls[0]
    assert kw["tools"] and kw["tool_choice"] == {"type": "none"}


async def test_max_tokens_stop_raises_truncated():
    chat, _ = _chat(_message([TEXT], stop_reason="max_tokens"))
    with pytest.raises(ModelOutputTruncated):
        await chat.complete([Turn("user", "q")], [])


async def test_refusal_raises_call_failed():
    chat, _ = _chat(_message([], stop_reason="refusal"))
    with pytest.raises(ModelCallFailed, match="declined"):
        await chat.complete([Turn("user", "q")], [])


@pytest.mark.parametrize("stop", ["end_turn", "tool_use", "stop_sequence", "pause_turn"])
async def test_other_stop_reasons_are_normal_replies(stop):
    chat, _ = _chat(_message([TEXT], stop_reason=stop))
    assert (await chat.complete([Turn("user", "q")], [])).content == "hi"


async def test_usage_counts_cache_reads(caplog):
    usage = {
        "input_tokens": 100,
        "cache_read_input_tokens": 900,
        "cache_creation_input_tokens": 200,
        "output_tokens": 300,
    }
    chat, _ = _chat(_message([TEXT], usage=usage))
    caplog.set_level("INFO")
    await chat.complete([Turn("user", "q")], [])
    assert "1200 prompt (900 cached) + 300 completion tokens in" in caplog.text
    assert chat.total_tokens == 1500


def test_foundry_endpoint_uses_the_foundry_client_with_entra(monkeypatch):
    async def token():
        return "tok"

    monkeypatch.setattr("k8s_servant.llm.messages.entra_token_provider", lambda scope: token)
    config = {
        "llm_provider": "anthropic",
        "llm_endpoint": "https://x.services.ai.azure.com",
        "llm_token_scope": "scope",
        "llm_timeout_seconds": 5,
    }
    client = build_messages_client(config, ModelAuth("entra", "", "Entra ID"))
    assert isinstance(client, AsyncAnthropicFoundry)
    assert str(client.base_url).startswith("https://x.services.ai.azure.com/anthropic")


def test_other_endpoint_uses_the_plain_client_with_the_key():
    config = {
        "llm_provider": "anthropic",
        "llm_endpoint": "https://api.anthropic.com",
        "llm_api_key": "k",
        "llm_timeout_seconds": 5,
    }
    client = build_messages_client(config, ModelAuth("api-key", "", "API key"))
    assert isinstance(client, AsyncAnthropic) and not isinstance(client, AsyncAnthropicFoundry)
    assert client.api_key == "k"


async def test_empty_reply_is_sent_back_as_a_placeholder_text_block():
    # An end_turn reply with no content must not come back as "content": [] (the API
    # rejects it); this is the health check's JSON retry case.
    chat, fake = _chat(_message([]), _message([TEXT]))
    first = await chat.complete([Turn("user", "q")], [])
    await chat.complete([Turn("user", "q"), first, Turn("user", "again")], [])
    assistant = fake.calls[1]["messages"][1]
    assert assistant == {"role": "assistant", "content": [{"type": "text", "text": "(no answer)"}]}
    TypeAdapter(MessageParam).validate_python(assistant)


async def test_assistant_turn_without_content_or_calls_sends_a_placeholder():
    chat, fake = _chat(_message([TEXT]))
    await chat.complete([Turn("user", "q"), Turn("assistant", ""), Turn("user", "again")], [])
    assert fake.calls[0]["messages"][1]["content"] == [{"type": "text", "text": "(no answer)"}]


async def test_minimal_effort_warns_once_that_low_is_used(caplog):
    # Spec: no silent degradation. The Messages API has no "minimal".
    chat, fake = _chat(_message([TEXT]), effort="minimal")
    await chat.complete([Turn("user", "q")], [])
    await chat.complete([Turn("user", "q")], [])
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "minimal" in r.getMessage()]
    assert len(warnings) == 1 and '"low"' in warnings[0].getMessage()
    assert fake.calls[0]["output_config"] == {"effort": "low"}


def test_other_efforts_do_not_warn(caplog):
    for effort in ("", "none", "low", "medium", "high", "xhigh", "max"):
        _chat(_message([TEXT]), effort=effort)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
