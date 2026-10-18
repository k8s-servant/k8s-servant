"""Test doubles shared by several test files (imported as `from fakes import ...`)."""

import json
from dataclasses import replace

from k8s_servant.llm.types import ToolCall, Turn


def answer(text: str) -> Turn:
    """A model reply with text and no tool calls."""
    return Turn("assistant", text)


def tool_call(name: str, args: dict, call_id: str = "c1") -> Turn:
    """A model reply asking for one tool call."""
    return Turn("assistant", tool_calls=[ToolCall(call_id, name, json.dumps(args))])


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

    total_tokens = 0

    async def complete(self, turns, tools, *, use_tools=True):
        self.calls.append({"turns": [replace(t) for t in turns], "tools": tools, "use_tools": use_tools})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply
