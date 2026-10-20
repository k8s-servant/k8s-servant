"""The table of supported model providers and the endpoint rules that go with it.

Which wire API a provider speaks, where it lives by default and how hard its models
think by default are facts about the provider, not about one request, so they sit in
one table that config.py (defaults, validation) and the API clients (endpoint
handling) both read. This module imports nothing from the rest of the package so
that config.py can import it without a cycle.
"""

from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Provider:
    """What is fixed about one LLM_PROVIDER value.

    Attributes:
        name: The LLM_PROVIDER value.
        api: The wire API the provider is called through.
        api_label: The same, as shown to people in logs and `doctor`.
        default_endpoint: Base URL used when LLM_ENDPOINT is empty; "" means the
            provider has no fixed address and LLM_ENDPOINT is required.
        default_effort: Default LLM_REASONING_EFFORT; "" = the parameter is not sent.
    """

    name: str
    api: Literal["responses", "messages", "chat_completions"]
    api_label: str
    default_endpoint: str
    default_effort: str


PROVIDERS: dict[str, Provider] = {
    "foundry": Provider("foundry", "responses", "Responses API", "", "medium"),
    "openai": Provider("openai", "responses", "Responses API", "https://api.openai.com/v1", "medium"),
    "anthropic": Provider("anthropic", "messages", "Messages API", "https://api.anthropic.com", "medium"),
    "ollama": Provider("ollama", "chat_completions", "Chat Completions API", "http://localhost:11434/v1", ""),
    "openai-compatible": Provider("openai-compatible", "chat_completions", "Chat Completions API", "", ""),
}

# The settings of the single-provider releases and the names that replace them.
# configure_app() refuses the old names instead of ignoring them: a silently ignored
# FOUNDRY_ENDPOINT would leave the agent without a model and with a confusing error.
OLD_SETTINGS: dict[str, str] = {
    "FOUNDRY_ENDPOINT": "LLM_ENDPOINT",
    "FOUNDRY_DEPLOYMENT": "LLM_MODEL",
    "FOUNDRY_API_KEY": "LLM_API_KEY",
    "FOUNDRY_API_KEY_FILE": "LLM_API_KEY_FILE",
    "FOUNDRY_TOKEN_SCOPE": "LLM_TOKEN_SCOPE",
    "FOUNDRY_TIMEOUT_SECONDS": "LLM_TIMEOUT_SECONDS",
    "FOUNDRY_REASONING_EFFORT": "LLM_REASONING_EFFORT",
}

_FOUNDRY_SUFFIXES = (".openai.azure.com", ".services.ai.azure.com", ".cognitiveservices.azure.com")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
# Providers that are typically run by the user, in the cluster or next to it.
_SELF_HOSTED = ("ollama", "openai-compatible")


def is_foundry_endpoint(endpoint: str) -> bool:
    """Whether the URL points at an Microsoft Foundry / Azure OpenAI resource."""
    host = urlsplit(endpoint.strip()).hostname or ""
    return host.endswith(_FOUNDRY_SUFFIXES)


def _plain_http_allowed(provider: str, host: str) -> bool:
    """Plain http is acceptable only where the traffic stays on the machine or in the cluster."""
    if host in _LOCAL_HOSTS:
        return True
    if provider not in _SELF_HOSTED:
        return False
    return "." not in host or host.endswith((".svc", ".svc.cluster.local"))


def normalize_endpoint(provider: str, endpoint: str) -> str:
    """Turn whatever endpoint form the user pasted into the provider's base URL.

    A bare Foundry host gets `/openai/v1`; a bare Foundry host with provider
    `anthropic` gets `/anthropic` (Claude in Foundry). Any URL that already has a
    path is used as given.

    Args:
        provider: The LLM_PROVIDER value.
        endpoint: The LLM_ENDPOINT value.

    Returns:
        The base URL with a trailing slash.

    Raises:
        ValueError: If the URL is not https. Plain http is accepted for localhost
            always, and for ollama and openai-compatible also for single-label hosts
            and in-cluster service names (`*.svc`, `*.svc.cluster.local`).
    """
    base = endpoint.strip().rstrip("/")
    parts = urlsplit(base)
    if parts.scheme != "https" and not (parts.scheme == "http" and _plain_http_allowed(provider, parts.hostname or "")):
        raise ValueError(
            f"LLM_ENDPOINT must start with https:// (http only for localhost and, for ollama and "
            f"openai-compatible, in-cluster hosts), got {endpoint!r}"
        )
    if not parts.path:
        if provider == "foundry":
            base += "/openai/v1"
        elif provider == "anthropic" and is_foundry_endpoint(base):
            base += "/anthropic"
    return base + "/"
