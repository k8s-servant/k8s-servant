"""How the agent signs in to the model: API key, Entra ID, or nothing.

LLM_AUTH=auto picks a key when one is configured, Entra ID where the endpoint is an
Azure resource, and no sign-in for self-hosted servers. The eligibility rule matters
for security: an Entra ID token is only ever minted for Foundry endpoints, so it can
never be sent to a third-party base URL. Keys are never logged; only their source.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal

from k8s_servant.llm.providers import is_foundry_endpoint

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelAuth:
    """The resolved sign-in method.

    Attributes:
        method: "api-key", "entra" or "none".
        source: Where the credential comes from (never the credential itself).
        label: The same, for `doctor` and logs.
        warning: A note for the operator when auto mode chose the key over an
            available Workload Identity; empty otherwise. `doctor` prints it.
    """

    method: Literal["api-key", "entra", "none"]
    source: str
    label: str
    warning: str = ""


def resolve_model_auth(config: dict) -> ModelAuth:
    """Apply the LLM_AUTH rules to the settings.

    Args:
        config: Settings from configure_app().

    Returns:
        The sign-in method to use.

    Raises:
        ValueError: The setting combination is invalid; the message names the setting.
    """
    provider, mode = config["llm_provider"], config["llm_auth"]
    key = config["llm_api_key"]
    eligible = provider == "foundry" or (
        provider in ("anthropic", "openai-compatible") and is_foundry_endpoint(config["llm_endpoint"])
    )
    if mode == "entra":
        if not eligible:
            raise ValueError(
                f"LLM_AUTH=entra needs an Azure AI Foundry endpoint, but LLM_PROVIDER={provider} "
                f"with this LLM_ENDPOINT is not one; use an API key (LLM_API_KEY_FILE)"
            )
        if key:
            raise ValueError("LLM_AUTH=entra conflicts with LLM_API_KEY / LLM_API_KEY_FILE; unset the key")
        return _entra(config)
    if mode == "api-key":
        if not key:
            raise ValueError("LLM_AUTH=api-key needs a key: set LLM_API_KEY_FILE")
        return _api_key(config)
    if key:
        if config["workload_identity"] and eligible:
            warning = (
                "Workload Identity is available but an API key is configured: using the key. "
                "Set LLM_AUTH=entra and remove the key to use Workload Identity."
            )
            logger.warning(warning)
            return replace(_api_key(config), warning=warning)
        return _api_key(config)
    if eligible:
        return _entra(config)
    if provider in ("ollama", "openai-compatible"):
        return ModelAuth("none", "", "no key")
    raise ValueError(f"LLM_PROVIDER={provider} needs an API key: set LLM_API_KEY_FILE")


def _api_key(config: dict) -> ModelAuth:
    source = config["llm_api_key_source"]
    return ModelAuth("api-key", source, f"API key ({source})")


def _entra(config: dict) -> ModelAuth:
    source = "Workload Identity" if config["workload_identity"] else "az login / DefaultAzureCredential"
    return ModelAuth("entra", source, f"Entra ID ({source})")


def entra_token_provider(scope: str) -> Callable[[], Awaitable[str]]:
    """An async, cached Entra ID token provider for the given scope.

    DefaultAzureCredential picks up Workload Identity in the cluster and `az login`
    locally. azure-identity is imported here, not at module level, because it is an
    optional extra.

    Args:
        scope: The token scope (LLM_TOKEN_SCOPE).

    Returns:
        A coroutine function returning a fresh (cached) token.

    Raises:
        ValueError: azure-identity is not installed.
    """
    try:
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except ImportError as exc:
        raise ValueError("Entra ID sign-in needs azure-identity: install k8s-servant[azure]") from exc
    # The provider is synchronous (it may call Azure) and caches the token, so run
    # it in a worker thread to keep the event loop free.
    sync_provider = get_bearer_token_provider(DefaultAzureCredential(), scope)

    async def token() -> str:
        """A fresh (cached) token; the SDK calls this before every request."""
        return await asyncio.to_thread(sync_provider)

    return token
