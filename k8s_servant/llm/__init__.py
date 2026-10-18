"""The chat model client: neutral types plus one module per wire API.

Callers import the neutral types from `k8s_servant.llm.types` and the factory
and error helpers from here; which API client is behind `build_chat` is a detail
of this package.
"""

import logging

from k8s_servant.llm.auth import resolve_model_auth
from k8s_servant.llm.chat_completions import ChatCompletionsChat, build_client
from k8s_servant.llm.errors import MODEL_ERRORS, explain_model_error, model_error_text
from k8s_servant.llm.providers import PROVIDERS, normalize_endpoint
from k8s_servant.llm.responses import ResponsesChat
from k8s_servant.llm.types import ChatModel, ModelOutputTruncated

logger = logging.getLogger(__name__)


def describe_model(config: dict) -> str:
    """One line naming provider, model, API and sign-in, for logs and `doctor`.

    Args:
        config: Settings from configure_app().

    Returns:
        E.g. "foundry / my-deployment via Responses API, Entra ID (Workload Identity)".

    Raises:
        ValueError: The sign-in settings are invalid.
    """
    provider = PROVIDERS[config["llm_provider"]]
    auth = resolve_model_auth(config)
    return f"{provider.name} / {config['llm_model']} via {provider.api_label}, {auth.label}"


def build_chat(config: dict) -> ChatModel:
    """Create the model client for the configured provider.

    Args:
        config: Settings from configure_app().

    Returns:
        A ChatModel on the provider's wire API.

    Raises:
        ValueError: Invalid sign-in settings, or azure-identity missing for Entra ID.
        NotImplementedError: The provider's API client is not available yet.
    """
    provider = PROVIDERS[config["llm_provider"]]
    auth = resolve_model_auth(config)
    normalize_endpoint(provider.name, config["llm_endpoint"])  # fail early on a bad URL
    if provider.api == "chat_completions":
        chat = ChatCompletionsChat(build_client(config, auth), config["llm_model"], config["llm_reasoning_effort"])
    elif provider.api == "responses":
        chat = ResponsesChat(build_client(config, auth), config["llm_model"], config["llm_reasoning_effort"])
    else:
        # Replaced by the Messages client (Task 5).
        raise NotImplementedError(f"{provider.api_label} client not implemented yet (provider {provider.name})")
    logger.info("Model: %s", describe_model(config))
    return chat


__all__ = [
    "MODEL_ERRORS",
    "ChatCompletionsChat",
    "ModelOutputTruncated",
    "ResponsesChat",
    "build_chat",
    "build_client",
    "describe_model",
    "explain_model_error",
    "model_error_text",
    "normalize_endpoint",
]
