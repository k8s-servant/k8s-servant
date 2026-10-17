"""Client for the chat model deployed in MS Foundry.

Foundry exposes an OpenAI-compatible "v1" endpoint
(https://<resource>.openai.azure.com/openai/v1/), so the official `openai` SDK is
used as-is: no Azure-specific SDK and no api-version parameter are needed. Other
OpenAI-compatible endpoints (experimental) work too: give their full base URL
(e.g. https://api.openai.com/v1) and an API key in FOUNDRY_API_KEY.

Authentication, in order of preference:
  1. Entra ID token (recommended). DefaultAzureCredential picks up Workload
     Identity in the cluster and `az login` on a laptop. The identity needs the
     "Cognitive Services OpenAI User" role on the Foundry resource.
  2. API key (FOUNDRY_API_KEY), for quick local experiments only.

We use the Chat Completions API (not the newer Responses API) because every chat
model in Foundry supports it, OpenAI and non-OpenAI alike, so switching models is
a configuration change only.
"""

import asyncio
import logging
import time
from urllib.parse import urlsplit

import openai
from azure.core.exceptions import ClientAuthenticationError
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessage

logger = logging.getLogger(__name__)

# What usually causes each HTTP status from Foundry, in words an admin can act on.
_STATUS_HINTS = {
    401: "authentication or permission failed. Either the token or key was rejected (locally run "
    "`az login` or check FOUNDRY_API_KEY; in the cluster check the Workload Identity client ID), or "
    "the identity lacks a data-plane role on the Foundry resource: assign 'Cognitive Services OpenAI "
    "User' and wait ~5 minutes for it to take effect.",
    403: "access denied by the Foundry resource's network rules (public access disabled or IP not "
    "allowed); check the private endpoint and that its DNS name resolves to the private IP.",
    404: "not found: FOUNDRY_DEPLOYMENT does not match a deployment name, "
    "or FOUNDRY_ENDPOINT points at the wrong resource.",
    429: "rate limited: the deployment's tokens-per-minute quota is exhausted; retry later or raise the quota.",
}


def normalize_endpoint(endpoint: str) -> str:
    """Turn whatever endpoint form the user pasted into the v1 base URL.

    A bare host is taken to be a Foundry resource and gets `/openai/v1`; a URL that
    already has a path (another OpenAI-compatible server) is used as given.

    Args:
        endpoint: e.g. `https://res.openai.azure.com`, with or without a trailing
            slash, with or without `/openai/v1`; or `https://api.openai.com/v1`.

    Returns:
        The base URL with a trailing slash, e.g. `https://res.openai.azure.com/openai/v1/`.

    Raises:
        ValueError: If the endpoint does not use https (plain http is accepted only
            for localhost, where the traffic never leaves the machine or pod).
    """
    base = endpoint.strip().rstrip("/")
    parts = urlsplit(base)
    local = parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not local:
        raise ValueError(f"FOUNDRY_ENDPOINT must start with https:// (http only for localhost), got {endpoint!r}")
    if not parts.path:
        base += "/openai/v1"
    return base + "/"


def build_client(config: dict) -> AsyncOpenAI:
    """Create the async OpenAI client pointed at the Foundry v1 endpoint.

    Args:
        config: Settings from configure_app().

    Returns:
        A configured AsyncOpenAI client.
    """
    if config["foundry_api_key"]:
        logger.info("Foundry auth: API key (FOUNDRY_API_KEY)")
        api_key = config["foundry_api_key"]
    else:
        logger.info("Foundry auth: Entra ID token (DefaultAzureCredential)")
        # get_bearer_token_provider returns a function that caches the token and
        # refreshes it shortly before it expires. It is synchronous (it may call
        # Azure), so we run it in a worker thread to keep the event loop free.
        sync_provider = get_bearer_token_provider(DefaultAzureCredential(), config["foundry_token_scope"])

        async def api_key() -> str:
            """A fresh (cached) Entra ID token; the SDK calls this before every request."""
            return await asyncio.to_thread(sync_provider)

    return AsyncOpenAI(
        base_url=normalize_endpoint(config["foundry_endpoint"]),
        api_key=api_key,
        timeout=config["foundry_timeout_seconds"],
        # The SDK retries 429 and 5xx responses with exponential backoff and
        # honours the Retry-After header.
        max_retries=3,
    )


class FoundryChat:
    """The one operation the agent needs from the model: continue a conversation."""

    def __init__(self, client: AsyncOpenAI, deployment: str, reasoning_effort: str = ""):
        self.client = client
        self.deployment = deployment
        self.reasoning_effort = reasoning_effort
        # Tokens used by this client so far (prompt + completion), for the summary
        # line and for sizing the deployment's tokens-per-minute capacity.
        self.total_tokens = 0

    async def complete(self, messages: list[dict], tools: list[dict]) -> ChatCompletionMessage:
        """Send the conversation (and tool definitions) and return the model's reply.

        No temperature is set on purpose: reasoning models (gpt-5.x) only accept
        the default, and the default is fine for the others.

        Args:
            messages: Chat Completions messages (system, user, assistant, tool).
            tools: Tool definitions; omitted from the request when empty.

        Returns:
            The assistant message: text content and/or tool calls.
        """
        kwargs = {"model": self.deployment, "messages": messages}
        if tools:
            kwargs["tools"] = tools
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
            logger.info(
                "model call: %d prompt (%d cached) + %d completion tokens (%d reasoning) in %.1fs",
                usage.prompt_tokens,
                cached,
                usage.completion_tokens,
                reasoning,
                time.monotonic() - started,
            )
        return response.choices[0].message


def build_chat(config: dict) -> FoundryChat:
    """Create the model client for the configured deployment and reasoning effort."""
    return FoundryChat(build_client(config), config["foundry_deployment"], config["foundry_reasoning_effort"])


def explain_model_error(exc: BaseException) -> str:
    """Translate a model-call failure into a short, actionable message."""
    status = getattr(exc, "status_code", None)
    if status in _STATUS_HINTS:
        return f"HTTP {status} {_STATUS_HINTS[status]}"
    if isinstance(exc, ClientAuthenticationError):
        return (
            "could not get an Entra ID token: run `az login` locally, "
            "or check the Workload Identity setup in the cluster."
        )
    # APITimeoutError is a subclass of APIConnectionError, so check it first.
    if isinstance(exc, openai.APITimeoutError):
        return "the request timed out; try again or raise FOUNDRY_TIMEOUT_SECONDS."
    if isinstance(exc, openai.APIConnectionError):
        return (
            "cannot reach FOUNDRY_ENDPOINT: check the URL, DNS and the firewall / private "
            "endpoint (docs/foundry-setup.md, section Networking)."
        )
    return f"{type(exc).__name__}: {exc}"
