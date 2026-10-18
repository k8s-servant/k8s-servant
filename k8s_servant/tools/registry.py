"""The common shape of every tool the model can call, and the registry holding them.

A *tool* is a function the model may ask us to run, described by a name, a
human-readable description and a JSON schema for its arguments. Tools come from
different places (the MCP server, the kubectl fallback, the knowledge loader);
the registry hides that, so the agent loop treats them all the same.

Two rules keep the loop robust:
  * ToolRegistry.run() never raises. Unknown tools, bad arguments and tool
    crashes come back as text starting with "ERROR:", which the model reads and
    reacts to (e.g. by trying another tool).
  * truncate_output() caps the size of what goes back to the model.
"""

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from k8s_servant.llm.types import ToolSpec

logger = logging.getLogger(__name__)

# OpenAI-compatible APIs only accept tool names matching this pattern.
_VALID_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


@dataclass(frozen=True)
class Tool:
    """One callable tool.

    Attributes:
        name: Unique name the model uses to call the tool.
        description: Tells the model what the tool does and when to use it.
        parameters: JSON schema of the arguments object.
        run: Async function taking the parsed arguments dict and returning text.
        surface: Where the tool comes from ("mcp", "kubectl", "knowledge");
            shown by `k8s-servant tools` and in the system prompt.
    """

    name: str
    description: str
    parameters: dict
    run: Callable[[dict], Awaitable[str]]
    surface: str


class ToolRegistry:
    """Holds the tools for one agent session."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def add(self, tool: Tool) -> bool:
        """Register a tool.

        The first tool registered under a name wins. MCP tools are added first
        (see runtime.py), so they take precedence over anything else.

        Returns:
            True if registered, False if skipped (the reason is logged).
        """
        if not _VALID_NAME.match(tool.name):
            logger.warning("Skipping tool with invalid name %r (from %s)", tool.name, tool.surface)
            return False
        if tool.name in self._tools:
            logger.warning(
                "Skipping duplicate tool %r from %s; already provided by %s",
                tool.name,
                tool.surface,
                self._tools[tool.name].surface,
            )
            return False
        self._tools[tool.name] = tool
        return True

    def tools(self) -> list[Tool]:
        """All registered tools, in registration order."""
        return list(self._tools.values())

    def surfaces(self) -> set[str]:
        """The distinct sources the registered tools come from."""
        return {tool.surface for tool in self._tools.values()}

    def specs(self) -> list[ToolSpec]:
        """Tool definitions in the provider-neutral shape; each API client converts them."""
        return [ToolSpec(t.name, t.description, t.parameters) for t in self._tools.values()]

    async def run(self, name: str, arguments_json: str) -> str:
        """Run a tool with the JSON-encoded arguments the model produced.

        Args:
            name: Tool name from the model's tool call.
            arguments_json: The call's `arguments` string (JSON object).

        Returns:
            The tool's text output, or a message starting with "ERROR:".
        """
        tool = self._tools.get(name)
        if tool is None:
            return f"ERROR: unknown tool {name!r}. Available tools: {', '.join(self._tools)}"
        try:
            arguments = json.loads(arguments_json) if arguments_json and arguments_json.strip() else {}
        except json.JSONDecodeError as exc:
            return f"ERROR: arguments for {name!r} are not valid JSON ({exc}). Send a JSON object."
        if not isinstance(arguments, dict):
            return f"ERROR: arguments for {name!r} must be a JSON object, got {type(arguments).__name__}."
        try:
            return await tool.run(arguments)
        except Exception as exc:  # a broken tool must not stop the investigation
            logger.warning("Tool %s failed: %s: %s", name, type(exc).__name__, exc)
            logger.debug("Tool %s traceback", name, exc_info=True)
            return f"ERROR: tool {name!r} failed: {type(exc).__name__}: {exc}"


def truncate_output(text: str, max_chars: int) -> str:
    """Cut text longer than max_chars, keeping the head and the tail.

    Both ends matter: the head of `kubectl get` has the column headers and the
    first rows, the tail of a log has the most recent lines. Two thirds of the
    budget go to the head, one third to the tail.
    """
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    omitted = len(text) - head - tail
    return (
        f"{text[:head]}\n\n[... {omitted} characters omitted; narrow the query "
        f"(namespace, label selector, --tail) to see them ...]\n\n{text[-tail:]}"
    )
