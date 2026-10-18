"""Chat Completions client for the chat model deployed in MS Foundry.

Foundry exposes an OpenAI-compatible "v1" endpoint
(https://<resource>.openai.azure.com/openai/v1/), so the official `openai` SDK is
used as-is: no Azure-specific SDK and no api-version parameter are needed. Other
OpenAI-compatible endpoints (experimental) work too: give their full base URL
(e.g. https://api.openai.com/v1) and an API key in LLM_API_KEY.

Authentication, in order of preference:
  1. Entra ID token (recommended). DefaultAzureCredential picks up Workload
     Identity in the cluster and `az login` on a laptop. The identity needs the
     "Cognitive Services OpenAI User" role on the Foundry resource.
  2. API key (LLM_API_KEY), for quick local experiments only.

We use the Chat Completions API (not the newer Responses API) because every chat
model in Foundry supports it, OpenAI and non-OpenAI alike, so switching models is
a configuration change only.

This module is the only place that converts the neutral Turn / ToolSpec types
(llm/types.py) to Chat Completions messages and back.
"""

import asyncio
import logging
import time

import openai
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncOpenAI

from k8s_servant.llm.providers import normalize_endpoint
from k8s_servant.llm.types import ModelOutputTruncated, ToolCall, ToolSpec, Turn, log_usage

logger = logging.getLogger(__name__)

# What usually causes each HTTP status from Foundry, in words an admin can act on.
_STATUS_HINTS = {
    401: "authentication or permission failed. Either the token or key was rejected (locally run "
    "`az login` or check LLM_API_KEY; in the cluster check the Workload Identity client ID), or "
    "the identity lacks a data-plane role on the Foundry resource: assign 'Cognitive Services OpenAI "
    "User' and wait ~5 minutes for it to take effect.",
    403: "access denied by the Foundry resource's network rules (public access disabled or IP not "
    "allowed); check the private endpoint and that its DNS name resolves to the private IP.",
    404: "not found: LLM_MODEL does not match a deployment name, or LLM_ENDPOINT points at the wrong resource.",
    429: "rate limited: the deployment's tokens-per-minute quota is exhausted; retry later or raise the quota.",
}


def build_client(config: dict) -> AsyncOpenAI:
    """Create the async OpenAI client pointed at the Foundry v1 endpoint.

    Args:
        config: Settings from configure_app().

    Returns:
        A configured AsyncOpenAI client.
    """
    if config["llm_api_key"]:
        logger.info("Model auth: API key (LLM_API_KEY)")
        api_key = config["llm_api_key"]
    else:
        logger.info("Model auth: Entra ID token (DefaultAzureCredential)")
        # get_bearer_token_provider returns a function that caches the token and
        # refreshes it shortly before it expires. It is synchronous (it may call
        # Azure), so we run it in a worker thread to keep the event loop free.
        sync_provider = get_bearer_token_provider(DefaultAzureCredential(), config["llm_token_scope"])

        async def api_key() -> str:
            """A fresh (cached) Entra ID token; the SDK calls this before every request."""
            return await asyncio.to_thread(sync_provider)

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
            reasoning = getattr(getattr(usage, "completion_tokens_details", None), "reasoning_tokens", None) or 0
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


def build_chat(config: dict) -> ChatCompletionsChat:
    """Create the model client for the configured deployment and reasoning effort."""
    return ChatCompletionsChat(build_client(config), config["llm_model"], config["llm_reasoning_effort"])


def explain_model_error(exc: BaseException) -> str:
    """Translate a model-call failure into a short, actionable message."""
    status = getattr(exc, "status_code", None)
    if status in _STATUS_HINTS:
        return f"HTTP {status} {_STATUS_HINTS[status]}"
    if isinstance(exc, ModelOutputTruncated):
        return "the reply hit the output limit: lower the reasoning effort or shorten the question."
    if isinstance(exc, ClientAuthenticationError):
        return (
            "could not get an Entra ID token: run `az login` locally, "
            "or check the Workload Identity setup in the cluster."
        )
    # APITimeoutError is a subclass of APIConnectionError, so check it first.
    if isinstance(exc, openai.APITimeoutError):
        return "the request timed out; try again or raise LLM_TIMEOUT_SECONDS."
    if isinstance(exc, openai.APIConnectionError):
        return (
            "cannot reach LLM_ENDPOINT: check the URL, DNS and the firewall / private "
            "endpoint (docs/foundry-setup.md, section Networking)."
        )
    return f"{type(exc).__name__}: {exc}"
