"""Messages API client for Claude models (Anthropic API and Foundry's Claude endpoint).

Why: Claude's native API keeps extended thinking, tool use and prompt caching working
together, which an OpenAI-compatible shim does not. Anthropic's own endpoint and
Foundry's `/anthropic/` endpoint speak the same protocol; only the client class and
the sign-in differ (see build_messages_client).

The agent keeps the conversation, so every request is stateless. Thinking blocks carry
a signature the API checks when they come back, so the assistant's content blocks travel
in `Turn.provider_state` exactly as the API returned them. Prompt caching is automatic
through the top-level `cache_control` parameter.

This module is the only place that converts the neutral Turn / ToolSpec types
(llm/types.py) to Messages API shapes and back.
"""

import json
import logging
import time

from anthropic import AsyncAnthropic, AsyncAnthropicFoundry

from k8s_servant.llm.auth import ModelAuth, entra_token_provider
from k8s_servant.llm.providers import is_foundry_endpoint, normalize_endpoint
from k8s_servant.llm.types import NO_ANSWER, ModelCallFailed, ModelOutputTruncated, ToolCall, ToolSpec, Turn, log_usage

logger = logging.getLogger(__name__)

# LLM_REASONING_EFFORT values that map onto the API's effort levels; "minimal" has none.
_EFFORT = {"minimal": "low", "low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}


def build_messages_client(config: dict, auth: ModelAuth) -> AsyncAnthropic | AsyncAnthropicFoundry:
    """Create the async Anthropic client for the configured endpoint.

    Args:
        config: Settings from configure_app().
        auth: The sign-in method from resolve_model_auth(); this function never
            decides it, so an Entra ID token is only minted when auth says so.

    Returns:
        AsyncAnthropicFoundry for a Foundry endpoint, AsyncAnthropic otherwise.

    Raises:
        ValueError: The sign-in method cannot be used with this client.
    """
    logger.info("Model auth: %s", auth.label)
    endpoint = config["llm_endpoint"]
    base_url = normalize_endpoint("anthropic", endpoint)
    common = {"base_url": base_url, "timeout": config["llm_timeout_seconds"], "max_retries": 3}
    if auth.method == "entra":
        return AsyncAnthropicFoundry(azure_ad_token_provider=entra_token_provider(config["llm_token_scope"]), **common)
    if auth.method != "api-key":
        raise ValueError("The Messages API needs an API key or Entra ID sign-in")
    if is_foundry_endpoint(endpoint):
        return AsyncAnthropicFoundry(api_key=config["llm_api_key"], **common)
    return AsyncAnthropic(api_key=config["llm_api_key"], **common)


class MessagesChat:
    """A ChatModel on the Messages API."""

    def __init__(
        self,
        client: AsyncAnthropic | AsyncAnthropicFoundry,
        model: str,
        reasoning_effort: str,
        max_output_tokens: int,
    ):
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self.total_tokens = 0

    async def complete(self, turns: list[Turn], tools: list[ToolSpec], *, use_tools: bool = True) -> Turn:
        """Send the conversation (and tool definitions) and return the model's reply.

        No temperature is set on purpose: reasoning models reject it.

        Args:
            turns: The conversation so far.
            tools: Tool definitions; omitted from the request when empty.
            use_tools: False keeps the definitions (the history refers to them) but
                sets tool_choice to "none" so the model cannot call any.

        Returns:
            The assistant turn: text, tool calls, and the raw content blocks as
            provider_state so signed thinking survives the next request.

        Raises:
            ModelOutputTruncated: If the reply hit the output token limit.
            ModelCallFailed: If the model declined to answer.
        """
        kwargs: dict = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "messages": _to_messages(turns),
            "cache_control": {"type": "ephemeral"},
        }
        system = "\n\n".join(t.content for t in turns if t.role == "system")
        if system:
            kwargs["system"] = system
        if self.reasoning_effort == "none":
            kwargs["thinking"] = {"type": "disabled"}
        elif self.reasoning_effort:
            kwargs["thinking"] = {"type": "adaptive"}
            kwargs["output_config"] = {"effort": _EFFORT[self.reasoning_effort]}
        if tools:
            kwargs["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters} for t in tools
            ]
            kwargs["tool_choice"] = {"type": "auto"} if use_tools else {"type": "none"}
        started = time.monotonic()
        response = await self.client.messages.create(**kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            cached = getattr(usage, "cache_read_input_tokens", None) or 0
            created = getattr(usage, "cache_creation_input_tokens", None) or 0
            prompt = usage.input_tokens + cached + created
            self.total_tokens += prompt + usage.output_tokens
            log_usage(prompt, cached, usage.output_tokens, 0, time.monotonic() - started)
        if response.stop_reason == "refusal":
            raise ModelCallFailed("the model declined to answer (refusal)")
        if response.stop_reason == "max_tokens":
            raise ModelOutputTruncated("the reply hit the output token limit (max_tokens)")
        text = "".join(b.text for b in response.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, json.dumps(b.input)) for b in response.content if b.type == "tool_use"]
        # exclude_none: the input types reject the explicit nulls the output models carry.
        state = [b.model_dump(mode="json", exclude_none=True) for b in response.content]
        return Turn("assistant", text, calls, provider_state=state)


def _to_messages(turns: list[Turn]) -> list[dict]:
    """The non-system turns as Messages API messages.

    Consecutive tool turns merge into one user message: the API wants all results of a
    parallel tool call together, directly after the assistant message.
    """
    messages: list[dict] = []
    results: list[dict] | None = None  # the open tool_result message, while tool turns follow each other
    for turn in turns:
        if turn.role == "system":
            continue
        if turn.role == "tool":
            block = {"type": "tool_result", "tool_use_id": turn.tool_call_id, "content": turn.content}
            if results is None:
                results = [block]
                messages.append({"role": "user", "content": results})
            else:
                results.append(block)
            continue
        results = None
        if turn.role == "assistant":
            messages.append({"role": "assistant", "content": _assistant_blocks(turn)})
        else:
            messages.append({"role": "user", "content": turn.content})
    return messages


def _assistant_blocks(turn: Turn) -> list[dict]:
    """An assistant turn as content blocks.

    An empty reply (end_turn with no content) still needs one block: the API rejects
    an assistant message with empty content, and the health check's JSON retry sends
    exactly that history back.
    """
    if turn.provider_state is not None:
        blocks = list(turn.provider_state)
    else:
        blocks = []
        if turn.content:  # a tool-only turn must not send an empty text block
            blocks.append({"type": "text", "text": turn.content})
        blocks += [
            {"type": "tool_use", "id": c.id, "name": c.name, "input": json.loads(c.arguments)} for c in turn.tool_calls
        ]
    return blocks or [{"type": "text", "text": NO_ANSWER}]
