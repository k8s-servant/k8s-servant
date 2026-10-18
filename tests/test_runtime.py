from fakes import ScriptedChat, answer
from mcp.server import MCPServer

from k8s_servant.config import configure_app
from k8s_servant.knowledge import Knowledge, parse_skill
from k8s_servant.memory import Memory
from k8s_servant.runtime import open_agent, open_runtime


def _server() -> MCPServer:
    server = MCPServer("fake")

    @server.tool()
    def pods_list(namespace: str) -> str:
        """List pods."""
        return "ok"

    return server


async def test_falls_back_to_kubectl_when_mcp_is_unreachable():
    config = configure_app({"MCP_URL": "http://127.0.0.1:9/mcp"})
    async with open_runtime(config, Knowledge()) as runtime:
        assert not runtime.mcp_available
        assert [t.name for t in runtime.registry.tools()] == ["kubectl", "cluster_problems", "warning_events"]
        assert not runtime.registry.tools()[0].description.startswith("FALLBACK")


async def test_mcp_disabled_by_empty_url():
    async with open_runtime(configure_app({"MCP_URL": ""}), Knowledge()) as runtime:
        assert not runtime.mcp_available
        assert runtime.registry.surfaces() == {"kubectl", "cluster"}


async def test_prefers_mcp_and_adds_load_skill():
    knowledge = Knowledge(skills={"s": parse_skill("s", "---\ndescription: d\n---\nb", "t")})
    async with open_runtime(configure_app({}), knowledge, mcp_server=_server()) as runtime:
        names = [t.name for t in runtime.registry.tools()]
        assert names == ["pods_list", "kubectl", "cluster_problems", "warning_events", "load_skill"]
        assert runtime.mcp_available
        assert runtime.registry.tools()[1].description.startswith("FALLBACK")


async def test_unfiltered_mcp_events_list_is_replaced_by_warning_events():
    # events_list returns every event unfiltered; on a large cluster that exceeded the MCP
    # message limit. warning_events covers the same need, filtered and grouped.
    server = _server()

    @server.tool()
    def events_list(namespace: str = "") -> str:
        """List events."""
        return "all events"

    async with open_runtime(configure_app({}), Knowledge(), mcp_server=server) as runtime:
        names = [t.name for t in runtime.registry.tools()]
    assert "events_list" not in names and "warning_events" in names and "pods_list" in names


async def test_prometheus_and_memory_tools_are_registered_when_configured(tmp_path):
    config = configure_app({"MCP_URL": "", "PROMETHEUS_URL": "http://prom:9090", "ALERTMANAGER_URL": "http://am:9093"})
    async with open_runtime(config, Knowledge(), memory=Memory(tmp_path)) as runtime:
        names = [t.name for t in runtime.registry.tools()]
    assert names == [
        "kubectl",
        "cluster_problems",
        "warning_events",
        "prometheus_query",
        "prometheus_query_range",
        "alertmanager_alerts",
        "save_note",
    ]


async def test_open_agent_puts_learned_notes_in_the_prompt(tmp_path):
    memory = Memory(tmp_path)
    memory.add_note("CronJob backup fails on Sundays by design")
    chat = ScriptedChat(answer("hello"))
    async with open_agent(configure_app({"MCP_URL": ""}), Knowledge(), memory, chat=chat) as session:
        result = await session.agent.run("q")
        assert session.chat is chat and not session.mcp_available
        assert "save_note" in [t.name for t in session.registry.tools()]
    assert result.answer == "hello"
    assert "CronJob backup fails on Sundays by design" in chat.calls[0]["turns"][0].content
