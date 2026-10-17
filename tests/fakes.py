"""Test doubles shared by several test files (imported as `from fakes import ...`)."""

import json

from openai.types.chat import ChatCompletionMessage


def answer(text: str) -> ChatCompletionMessage:
    """A model reply with text and no tool calls."""
    return ChatCompletionMessage(role="assistant", content=text)


def tool_call(name: str, args: dict, call_id: str = "c1") -> ChatCompletionMessage:
    """A model reply asking for one tool call."""
    return ChatCompletionMessage(
        role="assistant",
        content=None,
        tool_calls=[{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}],
    )


def findings_answer(summary: str, findings: list[dict]) -> str:
    """A health-check answer ending with the findings JSON block."""
    return (
        "I checked the cluster.\n\n```json\n"
        + json.dumps({"summary": summary, "findings": findings}, indent=2)
        + "\n```\n"
    )


class ScriptedChat:
    """Fake model: returns the scripted replies in order and records every request.

    A reply that is an exception instance is raised instead of returned.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    async def complete(self, messages, tools):
        self.calls.append({"messages": [dict(m) for m in messages], "tools": tools})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply
