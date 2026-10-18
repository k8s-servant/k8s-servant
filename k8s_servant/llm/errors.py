"""Turn model-call failures into short, actionable messages.

Every provider SDK raises its own exception types; the CLI and the report catch
`MODEL_ERRORS` and show `model_error_text`, which names the setting to fix and
never contains the API key (providers sometimes echo it in their error body).
"""

import anthropic
import openai

from k8s_servant.llm.types import ModelOutputTruncated

_errors: list[type[BaseException]] = [openai.OpenAIError, anthropic.AnthropicError, ModelOutputTruncated]
try:
    from azure.core.exceptions import ClientAuthenticationError
except ImportError:  # the azure extra is optional
    ClientAuthenticationError = None
else:
    _errors.append(ClientAuthenticationError)

# The exceptions a model call can raise that are reported to the user, not crashes.
MODEL_ERRORS: tuple[type[BaseException], ...] = tuple(_errors)

# What usually causes each HTTP status from Foundry, in words an admin can act on.
_FOUNDRY_HINTS = {
    401: "authentication or permission failed. Either the token or key was rejected (locally run "
    "`az login` or check LLM_API_KEY_FILE; in the cluster check the Workload Identity client ID), or "
    "the identity lacks a data-plane role on the Foundry resource: assign 'Cognitive Services OpenAI "
    "User' and wait ~5 minutes for it to take effect.",
    403: "access denied by the Foundry resource's network rules (public access disabled or IP not "
    "allowed); check the private endpoint and that its DNS name resolves to the private IP.",
    404: "not found: LLM_MODEL does not match a deployment name, or LLM_ENDPOINT points at the wrong resource.",
    429: "rate limited: the deployment's tokens-per-minute quota is exhausted; retry later or raise the quota.",
}
_OTHER_HINTS = {
    401: "the API key was rejected (check LLM_API_KEY_FILE).",
    404: "LLM_MODEL is not a model this endpoint serves, or LLM_ENDPOINT is wrong.",
    429: "rate limited: retry later or check the account's limits.",
}


def explain_model_error(exc: BaseException, provider: str = "") -> str:
    """Translate a model-call failure into a short, actionable message.

    Args:
        exc: The exception raised by the model call.
        provider: The LLM_PROVIDER value; selects the Foundry-specific hints.

    Returns:
        One line for the log, the CLI or the report.
    """
    status = getattr(exc, "status_code", None)
    hints = _FOUNDRY_HINTS if provider == "foundry" else _OTHER_HINTS
    if status in hints:
        return f"HTTP {status} {hints[status]}"
    if isinstance(exc, ModelOutputTruncated):
        return "the reply hit the output limit: raise LLM_MAX_OUTPUT_TOKENS or lower LLM_REASONING_EFFORT."
    if ClientAuthenticationError is not None and isinstance(exc, ClientAuthenticationError):
        return (
            "could not get an Entra ID token: run `az login` locally, "
            "or check the Workload Identity setup in the cluster."
        )
    # The timeout errors are subclasses of the connection errors, so check them first.
    if isinstance(exc, openai.APITimeoutError | anthropic.APITimeoutError):
        return "the request timed out; try again or raise LLM_TIMEOUT_SECONDS."
    if isinstance(exc, openai.APIConnectionError | anthropic.APIConnectionError):
        return (
            "cannot reach LLM_ENDPOINT: check the URL, DNS and the firewall / private "
            "endpoint (docs/foundry-setup.md, section Networking)."
        )
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
    text = explain_model_error(exc, config["llm_provider"])
    key = config["llm_api_key"]
    return text.replace(key, "[redacted]") if key else text
