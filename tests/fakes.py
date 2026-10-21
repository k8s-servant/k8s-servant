"""Test doubles shared by several test files (imported as `from fakes import ...`)."""

import json
from dataclasses import replace
from pathlib import Path

from k8s_servant.llm.types import ToolCall, Turn
from k8s_servant.scan.inputs import INPUTS
from k8s_servant.tools.cluster_tools import Fetch


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


def fixture_fetch(flavor: str, calls: list | None = None, fail: dict[str, Exception] | None = None) -> Fetch:
    """A `Fetch` that answers scan inputs from tests/fixtures/scan/<flavor>/<name>.json.

    The file name is the scan input's name (matched against INPUTS), or
    `raw-prometheus` / `raw-alertmanager` for `get --raw` service-proxy paths. A
    missing file raises RuntimeError("NotFound"); `fail[name]` is raised instead.
    """
    root = Path(__file__).parent / "fixtures" / "scan" / flavor

    async def fetch(args: list[str]) -> dict:
        if calls is not None:
            calls.append(args)
        name = next((n for n, a, _ in INPUTS if a == args), None)
        if args[:2] == ["get", "--raw"]:
            name = (
                "raw-prometheus"
                if "/api/v1/query" in args[2]
                else "raw-alertmanager"
                if "/api/v2/alerts" in args[2]
                else None
            )
        if name in (fail or {}):
            raise fail[name]
        path = root / f"{name}.json"
        if name is None or not path.exists():
            raise RuntimeError("NotFound")
        return json.loads(path.read_text())

    return fetch
