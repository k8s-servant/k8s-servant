"""The agent loop: let the model think, call tools, and answer.

How one question is answered:

    messages = [system prompt, user question]
    repeat up to MAX_ITERATIONS times:
        reply = model(messages, tool definitions)
        if the reply has no tool calls  -> it is the answer, stop
        run each requested tool, append the (truncated) results, go again
    if the limit is reached: ask once more, without tools, for the best answer so far

The model never touches the cluster itself. It can only ask for tools from the
registry, and the registry only contains read-only tools.

The finished conversation is returned with the answer (AgentResult.messages), so
a caller can ask one more question in the same context with follow_up(); the
health check uses that when the model forgot the findings JSON.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from openai.types.chat import ChatCompletionMessage

from k8s_servant.knowledge import Knowledge, skill_catalog
from k8s_servant.tools.registry import ToolRegistry, truncate_output

logger = logging.getLogger(__name__)

BASE_PROMPT = """\
You are an experienced site reliability engineer (SRE) acting as a read-only
cluster administrator for the Kubernetes cluster "{cluster_name}". The people you
answer are the cluster administrators.

Rules:
- You are strictly read-only. You cannot and must not change anything. When something
  needs fixing, describe the fix (what, where, why) for the administrators to apply.
- Base every statement on evidence you gathered with your tools in this session:
  name the object, the field or event, and the time. If you could not check
  something, say so. Never invent object names, numbers or log lines.
- Copy object names character for character from tool output. Names in one cluster
  often differ by a single hyphen or suffix; when you look an object up again, use the
  exact name and check that the result is the object you meant.
- Before investigating, look at the skill catalog below. If a skill matches the task,
  load it with the load_skill tool and follow it.
- Use the environment description, cluster notes and learned notes: they explain
  what is normal here and things the cluster API cannot show.
- When you learn a lasting fact that will matter in later runs (for example that
  something which looks wrong is normal here), save it with save_note if that tool
  is available. Never save secrets.
- Work on your own until the task is done: follow every lead with your tools (describe
  the object, read its events and logs, check related alerts) and come back with
  conclusions. Never end with an offer such as "if you want, I can check ..."; check
  it, or list it under what you could not check and why.
- Keep tool output small. For overall health start with cluster_problems and
  warning_events (compact summaries) instead of listing every pod, deployment or
  event; then narrow by namespace, label or field selectors, and limit logs with a tail.
- Times are in UTC unless you say otherwise. The current time is {now}.
- Answer in concise Markdown: the conclusion first, then the evidence, then the
  suggested next steps.

Tools available in this session: {surfaces}."""

# Human-readable names of the tool surfaces, for the system prompt.
SURFACE_NAMES = {
    "mcp": "Kubernetes MCP tools (preferred for reading the cluster)",
    "cluster": "cluster_problems and warning_events (compact summaries; start here for health questions)",
    "kubectl": "kubectl (read-only)",
    "prometheus": "Prometheus/Alertmanager (metrics and firing alerts)",
    "knowledge": "load_skill",
    "memory": "save_note (remember lasting facts)",
}

LIMIT_MESSAGE = (
    "You have reached the limit of tool calls for this question. Do not call more tools. "
    "Answer now with what you found so far, and list what you could not check."
)


def build_system_prompt(
    cluster_name: str, knowledge: Knowledge, surfaces: set[str], now: datetime | None = None, *, notes: str = ""
) -> str:
    """Assemble the system prompt: rules, environment, cluster notes, learned notes, skill catalog.

    Args:
        cluster_name: Name used to address the cluster.
        knowledge: Loaded environment description, cluster notes and skills.
        surfaces: Tool surfaces live in this session (from ToolRegistry.surfaces()).
        now: Current time; injectable for tests.
        notes: Learned notes from memory (Memory.notes_for_prompt()).
    """
    now = now or datetime.now(UTC)
    surface_text = ", ".join(SURFACE_NAMES.get(s, s) for s in sorted(surfaces)) or "none"
    parts = [
        BASE_PROMPT.format(cluster_name=cluster_name, now=now.strftime("%Y-%m-%d %H:%M UTC"), surfaces=surface_text)
    ]
    if "kubectl" in surfaces and "mcp" not in surfaces:
        parts.append(
            "Note: the Kubernetes MCP server is not available in this session, so use the kubectl tool to read the cluster."
        )
    parts.append(
        "## Environment description\n\n"
        + (knowledge.environment or "No environment description was provided. Rely on what you can observe.")
    )
    if knowledge.cluster_notes:
        parts.append("## Cluster notes\n\n" + knowledge.cluster_notes)
    if notes:
        parts.append("## Learned notes\n\nFacts you saved in earlier runs:\n\n" + notes)
    parts.append("## Skill catalog\n\n" + skill_catalog(knowledge))
    return "\n\n".join(parts)


class ChatModel(Protocol):
    """Anything that can continue a conversation (FoundryChat, or a fake in tests)."""

    async def complete(self, messages: list[dict], tools: list[dict]) -> ChatCompletionMessage:
        """Send the conversation and the tool definitions; return the model's next message."""


@dataclass
class AgentResult:
    """Outcome of one question."""

    answer: str
    iterations: int  # model round-trips used (not counting the final limit call)
    tool_calls: int
    hit_limit: bool  # True when MAX_ITERATIONS was reached
    messages: list[dict] = field(default_factory=list)  # the whole conversation, for follow_up()


def _assistant_message(reply: ChatCompletionMessage) -> dict:
    """Copy only the fields the API accepts back; responses carry extra read-only fields."""
    message = {"role": "assistant", "content": reply.content}
    if reply.tool_calls:
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.function.name, "arguments": c.function.arguments}}
            for c in reply.tool_calls
        ]
    return message


def _short(text: str, limit: int = 200) -> str:
    """Shorten text for one log line."""
    return text if len(text) <= limit else text[:limit] + "..."


class Agent:
    """Runs the tool-calling loop for one question at a time."""

    def __init__(
        self,
        chat: ChatModel,
        registry: ToolRegistry,
        system_prompt: str,
        max_iterations: int,
        tool_output_max_chars: int,
    ):
        self.chat = chat
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.tool_output_max_chars = tool_output_max_chars

    async def run(self, question: str) -> AgentResult:
        """Answer one question, calling tools as the model requests."""
        messages: list[dict] = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": question},
        ]
        tool_calls = 0
        for iteration in range(1, self.max_iterations + 1):
            reply = await self.chat.complete(messages, self.registry.schemas())
            messages.append(_assistant_message(reply))
            if not reply.tool_calls:
                return AgentResult(reply.content or "", iteration, tool_calls, hit_limit=False, messages=messages)
            for call in reply.tool_calls:
                tool_calls += 1
                messages.append(await self._run_tool_call(call))
        logger.warning("Reached MAX_ITERATIONS=%d; asking the model for a final answer", self.max_iterations)
        messages.append({"role": "user", "content": LIMIT_MESSAGE})
        reply = await self.chat.complete(messages, [])
        messages.append(_assistant_message(reply))
        return AgentResult(reply.content or "", self.max_iterations, tool_calls, hit_limit=True, messages=messages)

    async def follow_up(self, messages: list[dict], text: str) -> str:
        """Ask one more question at the end of a finished conversation, without tools.

        Args:
            messages: AgentResult.messages of the finished run.
            text: The follow-up request.

        Returns:
            The model's text reply.
        """
        reply = await self.chat.complete([*messages, {"role": "user", "content": text}], [])
        return reply.content or ""

    async def _run_tool_call(self, call) -> dict:
        """Run one tool call and wrap its (truncated) output as a tool message."""
        started = time.monotonic()
        output = await self.registry.run(call.function.name, call.function.arguments)
        output = truncate_output(output, self.tool_output_max_chars)
        logger.info(
            "tool %s(%s) -> %d chars in %.1fs",
            call.function.name,
            _short(call.function.arguments),
            len(output),
            time.monotonic() - started,
        )
        logger.debug("tool %s output:\n%s", call.function.name, output)
        return {"role": "tool", "tool_call_id": call.id, "content": output}
