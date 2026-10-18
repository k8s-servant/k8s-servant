"""Turn model-call failures into short, actionable messages.

Every provider SDK raises its own exception types; the CLI and the report catch
`MODEL_ERRORS` and show `model_error_text`, which names the setting to fix and
never contains the API key (providers sometimes echo it in their error body).
"""

import anthropic
import openai

from k8s_servant.llm.auth import resolve_model_auth
from k8s_servant.llm.providers import is_foundry_endpoint
from k8s_servant.llm.types import ModelCallFailed, ModelOutputTruncated

_errors: list[type[BaseException]] = [
    openai.OpenAIError,
    anthropic.AnthropicError,
    ModelOutputTruncated,
    ModelCallFailed,
]
try:
    from azure.core.exceptions import ClientAuthenticationError
except ImportError:  # the azure extra is optional
    ClientAuthenticationError = None
else:
    _errors.append(ClientAuthenticationError)

# The exceptions a model call can raise that are reported to the user, not crashes.
MODEL_ERRORS: tuple[type[BaseException], ...] = tuple(_errors)

# The data-plane roles a Foundry identity needs, per model family. Microsoft Learn:
# https://learn.microsoft.com/azure/foundry/foundry-models/how-to/configure-entra-id#troubleshooting
_ROLES = (
    "'Cognitive Services OpenAI User' for OpenAI models, 'Cognitive Services User' for Claude and "
    "other non-OpenAI models"
)
# Foundry's 401, worded by sign-in method: it covers both a rejected credential and a
# missing data-plane role. "" is used when the sign-in settings cannot be resolved.
_FOUNDRY_401 = {
    "entra": "the Entra ID token was rejected (locally run `az login`; in the cluster check the Workload "
    f"Identity client ID), or the identity lacks a data-plane role on the Foundry resource ({_ROLES}); "
    "a new role takes ~5 minutes to take effect.",
    "api-key": "the API key was rejected: check LLM_API_KEY_FILE holds a key of this Foundry resource.",
    "": "authentication or permission failed. Either the token or key was rejected (locally run "
    "`az login` or check LLM_API_KEY_FILE; in the cluster check the Workload Identity client ID), or "
    f"the identity lacks a data-plane role on the Foundry resource ({_ROLES}); a new role takes ~5 "
    "minutes to take effect.",
}
# What usually causes each other HTTP status from Foundry, in words an admin can act on.
_FOUNDRY_HINTS = {
    403: "access denied: usually the Foundry resource's network rules (public access disabled or IP not "
    "allowed); check the private endpoint and that its DNS name resolves to the private IP. With Entra ID "
    f"it can also be a missing data-plane role ({_ROLES}).",
    404: "not found: LLM_MODEL does not match a deployment name, or LLM_ENDPOINT points at the wrong resource.",
    429: "rate limited: the deployment's tokens-per-minute quota is exhausted; retry later or raise the quota.",
}
_OTHER_HINTS = {
    401: "the API key was rejected (check LLM_API_KEY_FILE).",
    404: "LLM_MODEL is not a model this endpoint serves, or LLM_ENDPOINT is wrong.",
    429: "rate limited: retry later or check the account's limits.",
}


def explain_model_error(exc: BaseException, provider: str = "", *, endpoint: str = "", auth_method: str = "") -> str:
    """Translate a model-call failure into a short, actionable message.

    The text may contain the provider's error body, which can echo the API key:
    callers outside this module use model_error_text, which removes it.

    Args:
        exc: The exception raised by the model call.
        provider: The LLM_PROVIDER value; picks the API-specific advice.
        endpoint: The LLM_ENDPOINT value; a Foundry host gets the Foundry hints
            whatever the provider (Claude and partner models on Foundry).
        auth_method: The resolved sign-in ("entra", "api-key", "none"), which words
            the Foundry 401 hint; "" when unknown.

    Returns:
        One line for the log, the CLI or the report.
    """
    status = getattr(exc, "status_code", None)
    foundry = provider == "foundry" or is_foundry_endpoint(endpoint)
    if foundry and status == 401:
        return f"HTTP 401 {_FOUNDRY_401.get(auth_method, _FOUNDRY_401[''])}"
    hints = _FOUNDRY_HINTS if foundry else _OTHER_HINTS
    if status in hints:
        return f"HTTP {status} {hints[status]}"
    if isinstance(exc, ModelCallFailed):
        return str(exc)
    if isinstance(exc, ModelOutputTruncated):
        # Only the Messages API sends an output limit (LLM_MAX_OUTPUT_TOKENS).
        if provider == "anthropic":
            return "the reply hit the output limit: raise LLM_MAX_OUTPUT_TOKENS or lower LLM_REASONING_EFFORT."
        return "the reply hit the output limit: lower LLM_REASONING_EFFORT or shorten the question."
    if ClientAuthenticationError is not None and isinstance(exc, ClientAuthenticationError):
        return (
            "could not get an Entra ID token: run `az login` locally, "
            "or check the Workload Identity setup in the cluster."
        )
    # The timeout errors are subclasses of the connection errors, so check them first.
    if isinstance(exc, openai.APITimeoutError | anthropic.APITimeoutError):
        return "the request timed out; try again or raise LLM_TIMEOUT_SECONDS."
    if isinstance(exc, openai.APIConnectionError | anthropic.APIConnectionError):
        docs = "docs/foundry-setup.md, section Networking" if foundry else "docs/model-providers.md"
        return f"cannot reach LLM_ENDPOINT: check the URL, DNS and the firewall / private endpoint ({docs})."
    if status is not None:
        return f"HTTP {status}: {getattr(exc, 'message', None) or exc}"
    return f"{type(exc).__name__}: {exc}"


def model_error_text(exc: BaseException, config: dict) -> str:
    """explain_model_error for the configured provider, with the API key removed.

    Args:
        exc: The exception raised by the model call.
        config: Settings from configure_app().

    Returns:
        The message, safe to log and to post.
    """
    try:
        auth_method = resolve_model_auth(config).method
    except ValueError:  # invalid sign-in settings: the hint stays neutral
        auth_method = ""
    text = explain_model_error(exc, config["llm_provider"], endpoint=config["llm_endpoint"], auth_method=auth_method)
    key = config["llm_api_key"]
    return text.replace(key, "[redacted]") if key else text
