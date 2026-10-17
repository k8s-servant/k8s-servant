"""Wires the tools, the system prompt and the model client for one session.

A session is one CLI command or one scheduled run. Tools are registered in this
order:
  1. MCP tools first, so they win any name clash and are the preferred surface.
  2. The kubectl tool, always present. When MCP is up its description marks it
     as a fallback; when MCP is down it is the only way to read the cluster.
     Then cluster_problems and warning_events, compact summaries that also work
     on clusters too large to list (they use kubectl too).
  3. Prometheus / Alertmanager tools, when their URLs are configured.
  4. load_skill, when there are skills.
  5. save_note, when the session has a memory.

open_runtime() gives just the tools (used by `tools` and `doctor`); open_agent()
adds the system prompt (with learned notes) and the model client, ready to run.
The MCP connection stays open for the duration of the `async with` block.
"""

import logging
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from k8s_servant.agent import Agent, ChatModel, build_system_prompt
from k8s_servant.knowledge import Knowledge, build_load_skill_tool
from k8s_servant.llm import build_chat
from k8s_servant.memory import Memory, build_save_note_tool
from k8s_servant.tools.cluster_tools import build_cluster_tools
from k8s_servant.tools.kubectl_tool import build_kubectl_tool
from k8s_servant.tools.mcp_tools import connect_mcp
from k8s_servant.tools.prometheus_tool import build_prometheus_tools
from k8s_servant.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# MCP tools not offered to the model. events_list returns every event of every
# namespace unfiltered; on a large cluster that exceeds the MCP message limit
# in practice. warning_events covers the same need, filtered and grouped.
REPLACED_MCP_TOOLS = {"events_list"}


@dataclass
class Runtime:
    """The tools available in this session."""

    registry: ToolRegistry
    mcp_available: bool


@dataclass
class Session:
    """Everything one run needs: the agent, its model client and its tools."""

    agent: Agent
    chat: ChatModel
    registry: ToolRegistry
    mcp_available: bool


@asynccontextmanager
async def open_runtime(config: dict, knowledge: Knowledge, mcp_server=None, memory: Memory | None = None):
    """Connect to MCP (if configured) and build the tool registry.

    Args:
        config: Settings from configure_app().
        knowledge: Loaded knowledge (for the load_skill tool).
        mcp_server: Override for config["mcp_url"]; tests pass an in-process server.
        memory: When given, the save_note tool is registered.

    Yields:
        Runtime with the registry and whether MCP is available.
    """
    async with AsyncExitStack() as stack:
        server = mcp_server if mcp_server is not None else config["mcp_url"]
        if server:
            mcp_tools = await connect_mcp(stack, server, timeout_seconds=config["mcp_timeout_seconds"])
        else:
            logger.info("MCP disabled (MCP_URL is empty); using kubectl only")
            mcp_tools = []
        registry = ToolRegistry()
        for tool in mcp_tools:
            if tool.name not in REPLACED_MCP_TOOLS:
                registry.add(tool)
        registry.add(
            build_kubectl_tool(config["kubectl_path"], config["kubectl_timeout_seconds"], mcp_available=bool(mcp_tools))
        )
        for tool in build_cluster_tools(
            config["kubectl_path"], config["kubectl_timeout_seconds"], config["events_exclude_reasons"]
        ):
            registry.add(tool)
        for tool in build_prometheus_tools(
            config["prometheus_url"], config["alertmanager_url"], config["prometheus_timeout_seconds"]
        ):
            registry.add(tool)
        if knowledge.skills:
            registry.add(build_load_skill_tool(knowledge))
        if memory is not None:
            registry.add(build_save_note_tool(memory))
        yield Runtime(registry=registry, mcp_available=bool(mcp_tools))


@asynccontextmanager
async def open_agent(
    config: dict, knowledge: Knowledge, memory: Memory, chat: ChatModel | None = None, mcp_server=None
):
    """Open a session: tools, system prompt with learned notes, model client, agent.

    Args:
        config: Settings from configure_app().
        knowledge: Loaded knowledge.
        memory: The agent's memory (learned notes, save_note).
        chat: Model client; by default a FoundryChat for the configured deployment.
        mcp_server: Override for config["mcp_url"] (tests).

    Yields:
        Session.

    Raises:
        ValueError: If FOUNDRY_ENDPOINT is invalid (only when chat is not given).
    """
    if chat is None:
        chat = build_chat(config)
    async with open_runtime(config, knowledge, mcp_server=mcp_server, memory=memory) as runtime:
        prompt = build_system_prompt(
            config["cluster_name"], knowledge, runtime.registry.surfaces(), notes=memory.notes_for_prompt()
        )
        agent = Agent(chat, runtime.registry, prompt, config["max_iterations"], config["tool_output_max_chars"])
        yield Session(agent=agent, chat=chat, registry=runtime.registry, mcp_available=runtime.mcp_available)
