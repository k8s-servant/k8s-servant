"""Chat Completions client for the ollama and openai-compatible providers.

Why Chat Completions there: it is the one API that self-hosted servers (Ollama,
vLLM, LM Studio), gateways such as LiteLLM, and partner models offering only that
API all speak, so any of them works with a base URL and nothing else. Foundry and
OpenAI use the Responses API (responses.py) and Claude the Messages API
(messages.py) instead.

build_client() also creates the AsyncOpenAI client the Responses API uses: the
official `openai` SDK talks to OpenAI and to Foundry's OpenAI-compatible "v1"
endpoint as-is, with no Azure-specific SDK or api-version parameter. Sign-in (key,
Entra ID or none) is decided by llm/auth.py and passed in.

This module is the only place that converts the neutral Turn / ToolSpec types
(llm/types.py) to Chat Completions messages and back.
"""

import logging
import time

from openai import AsyncOpenAI

from k8s_servant.llm.auth import ModelAuth, entra_token_provider
from k8s_servant.llm.providers import normalize_endpoint
from k8s_servant.llm.types import ModelOutputTruncated, ToolCall, ToolSpec, Turn, log_usage

logger = logging.getLogger(__name__)


def build_client(config: dict, auth: ModelAuth) -> AsyncOpenAI:
    """Create the async OpenAI client for the configured endpoint.

    Args:
        config: Settings from configure_app().
        auth: The sign-in method from resolve_model_auth(); this function never
            decides it, so an Entra ID token is only minted when auth says so.

    Returns:
        A configured AsyncOpenAI client.
    """
    logger.info("Model auth: %s", auth.label)
    if auth.method == "entra":
        api_key = entra_token_provider(config["llm_token_scope"])
    elif auth.method == "api-key":
        api_key = config["llm_api_key"]
    else:
        api_key = "unused"  # the SDK requires a value; keyless servers ignore it

    return AsyncOpenAI(
        base_url=normalize_endpoint(config["llm_provider"], config["llm_endpoint"]),
        api_key=api_key,
        timeout=config["llm_timeout_seconds"],
        # The SDK retries 429 and 5xx responses with exponential backoff and
        # honours the Retry-After header.
        max_retries=3,
    )


class ChatCompletionsChat:
    """A ChatModel on the Chat Completions API."""

    def __init__(self, client: AsyncOpenAI, model: str, reasoning_effort: str):
        self.client = client
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.total_tokens = 0

    async def complete(self, turns: list[Turn], tools: list[ToolSpec], *, use_tools: bool = True) -> Turn:
        """Send the conversation (and tool definitions) and return the model's reply.

        No temperature is set on purpose: reasoning models (gpt-5.x) only accept
        the default, and the default is fine for the others.

        Args:
            turns: The conversation so far.
            tools: Tool definitions; omitted from the request when empty.
            use_tools: False omits the definitions, which is how this API forbids
                tool calls (it has no equivalent of a "none" choice that keeps them).

        Returns:
            The assistant turn: text content and/or tool calls.

        Raises:
            ModelOutputTruncated: If the reply hit the output token limit.
        """
        kwargs = {"model": self.model, "messages": [_to_message(t) for t in turns]}
        if tools and use_tools:
            kwargs["tools"] = [_to_tool(t) for t in tools]
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort
        started = time.monotonic()
        response = await self.client.chat.completions.create(**kwargs)
        usage = getattr(response, "usage", None)
        if usage is not None:
            reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None)
            # Prompt tokens served from Foundry's prompt cache are billed at a discount.
            cached = getattr(getattr(usage, "prompt_tokens_details", None), "cached_tokens", None) or 0
            self.total_tokens += usage.prompt_tokens + usage.completion_tokens
            log_usage(usage.prompt_tokens, cached, usage.completion_tokens, reasoning, time.monotonic() - started)
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise ModelOutputTruncated("the reply hit the output token limit (finish_reason=length)")
        message = choice.message
        calls = [ToolCall(c.id, c.function.name, c.function.arguments) for c in message.tool_calls or []]
        return Turn("assistant", message.content or "", calls)


def _to_tool(spec: ToolSpec) -> dict:
    """A tool definition in the Chat Completions format."""
    return {
        "type": "function",
        "function": {"name": spec.name, "description": spec.description, "parameters": spec.parameters},
    }


def _to_message(turn: Turn) -> dict:
    """A turn as a Chat Completions message, with only the fields the API accepts back."""
    if turn.role == "tool":
        return {"role": "tool", "tool_call_id": turn.tool_call_id, "content": turn.content}
    message = {"role": turn.role, "content": turn.content}
    if turn.tool_calls:
        message["content"] = turn.content or None
        message["tool_calls"] = [
            {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
            for c in turn.tool_calls
        ]
    return message
