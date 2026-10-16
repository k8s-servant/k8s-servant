"""Tools from kubernetes-mcp-server, via the Model Context Protocol (MCP).

MCP is a standard way for a server to publish tools that an AI agent can call.
We connect to the server once per session, ask which tools it has (list_tools)
and wrap each one as a registry Tool whose run() forwards the call (call_tool).
Names, descriptions and argument schemas come from the server unchanged, so new
server versions bring new tools without code changes here.

The server itself enforces read-only mode (`read_only = true` in its TOML
config) and hides Secrets (`[[denied_resources]]`); see deploy/local/mcp-config.toml.

Fallback: when the server cannot be reached, connect_mcp() logs a warning and
returns no tools. The kubectl tool is then the only way to read the cluster.
"""

import json
import logging
from contextlib import AsyncExitStack

from mcp import Client

from k8s_servant.tools.registry import Tool

logger = logging.getLogger(__name__)


def _root_cause(exc: BaseException) -> str:
    """Connection errors arrive wrapped in ExceptionGroups; show the first real one."""
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    return f"{type(exc).__name__}: {exc}"


def _result_to_text(result) -> str:
    """Flatten an MCP CallToolResult into plain text for the model."""
    parts = []
    for block in result.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
        else:
            parts.append(f"[{getattr(block, 'type', 'unknown')} content omitted]")
    if not parts and result.structured_content is not None:
        parts.append(json.dumps(result.structured_content, indent=2, default=str))
    text = "\n".join(parts) or "(no output)"
    return f"ERROR: {text}" if result.is_error else text


def _make_runner(client: Client, tool_name: str):
    """Build the run() function for one MCP tool (a closure over the open client)."""

    async def run(arguments: dict) -> str:
        """Forward one call to the MCP server; a failure becomes ERROR text with advice."""
        try:
            result = await client.call_tool(tool_name, arguments)
        except Exception as exc:
            # Typical cause: an answer too large for one MCP message (all events of a
            # big cluster). Tell the model how to continue instead of only what broke.
            logger.warning("MCP tool %s failed: %s", tool_name, _root_cause(exc))
            return (
                f"ERROR: MCP tool {tool_name!r} failed: {_root_cause(exc)}. "
                "Retry with narrower arguments (one namespace, a label or field selector), "
                "or use another tool (the kubectl tool or a compact summary tool) for the same data."
            )
        return _result_to_text(result)

    return run


async def connect_mcp(stack: AsyncExitStack, server, timeout_seconds: float | None = None) -> list[Tool]:
    """Connect to an MCP server and return its tools.

    Args:
        stack: Exit stack that owns the connection; the connection is closed
            when the stack closes (at the end of the CLI command).
        server: Server URL (e.g. http://localhost:8080/mcp, streamable HTTP), or
            an in-process server object (used by tests).
        timeout_seconds: Maximum wait for any single MCP response (None = the
            library default, several minutes).

    Returns:
        The server's tools, or an empty list if the server is unreachable.
    """
    try:
        client = await stack.enter_async_context(Client(server, read_timeout_seconds=timeout_seconds))
        listed, cursor = [], None
        while True:  # the tool list can be paginated
            page = await client.list_tools(cursor=cursor)
            listed.extend(page.tools)
            cursor = page.next_cursor
            if not cursor:
                break
    except Exception as exc:
        logger.warning("MCP server %s is not reachable, using the kubectl fallback (%s)", server, _root_cause(exc))
        return []
    tools = [
        Tool(
            name=t.name,
            description=t.description or t.title or t.name,
            parameters=t.input_schema or {"type": "object", "properties": {}},
            run=_make_runner(client, t.name),
            surface="mcp",
        )
        for t in listed
    ]
    logger.info("MCP server %s: %d tools", server, len(tools))
    return tools
