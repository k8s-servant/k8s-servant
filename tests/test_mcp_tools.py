from contextlib import AsyncExitStack

from mcp.server import MCPServer

from k8s_servant.tools.mcp_tools import connect_mcp


def _fake_server() -> MCPServer:
    """An in-process MCP server with one working and one failing tool."""
    server = MCPServer("fake-kubernetes")

    @server.tool()
    def pods_list(namespace: str) -> str:
        """List pods in a namespace."""
        return f"pod-a Running\npod-b CrashLoopBackOff ({namespace})"

    @server.tool()
    def broken() -> str:
        """Always fails."""
        raise RuntimeError("kaput")

    return server


async def test_tools_are_wrapped_and_callable():
    async with AsyncExitStack() as stack:
        tools = {t.name: t for t in await connect_mcp(stack, _fake_server())}
        assert set(tools) == {"pods_list", "broken"}
        pods = tools["pods_list"]
        assert pods.surface == "mcp"
        assert pods.description == "List pods in a namespace."
        assert pods.parameters["required"] == ["namespace"]
        assert await pods.run({"namespace": "x"}) == "pod-a Running\npod-b CrashLoopBackOff (x)"


async def test_tool_errors_become_error_text():
    async with AsyncExitStack() as stack:
        tools = {t.name: t for t in await connect_mcp(stack, _fake_server())}
        assert (await tools["broken"].run({})).startswith("ERROR:")


async def test_unreachable_server_returns_no_tools(caplog):
    async with AsyncExitStack() as stack:
        assert await connect_mcp(stack, "http://127.0.0.1:9/mcp") == []
    assert "not reachable" in caplog.text


async def test_slow_mcp_tool_times_out():
    # Final review: without a read timeout a stalled MCP call hangs ~300s per call.
    import asyncio
    import time

    server = MCPServer("slow")

    @server.tool()
    async def pods_log(name: str) -> str:
        """Hangs."""
        await asyncio.sleep(10)
        return "late"

    async with AsyncExitStack() as stack:
        tools = {t.name: t for t in await connect_mcp(stack, server, timeout_seconds=1)}
        started = time.monotonic()
        text = await tools["pods_log"].run({"name": "x"})
        assert text.startswith("ERROR: MCP tool 'pods_log' failed")
        assert time.monotonic() - started < 5


async def test_failed_mcp_call_tells_the_model_how_to_continue():
    # Seen on a large cluster: events_list for all namespaces exceeded the MCP client's 1 MB
    # message limit and the model gave up instead of narrowing or using kubectl.
    from k8s_servant.tools.mcp_tools import _make_runner

    class BrokenClient:
        async def call_tool(self, name, arguments):
            raise RuntimeError("SSE stream failed: Server-sent event exceeded the 1048576 byte limit.")

    text = await _make_runner(BrokenClient(), "events_list")({})
    assert text.startswith("ERROR: MCP tool 'events_list' failed: RuntimeError: SSE stream failed")
    assert "narrow" in text and "kubectl" in text
