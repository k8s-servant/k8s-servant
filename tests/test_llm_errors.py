import openai
import pytest

from k8s_servant.config import configure_app
from k8s_servant.llm.errors import MODEL_ERRORS, explain_model_error, model_error_text
from k8s_servant.llm.types import ModelCallFailed, ModelOutputTruncated


def make_status_error(status, body="boom"):
    """An openai.APIStatusError of the given status, as the SDK raises it."""
    import httpx2

    request = httpx2.Request("POST", "https://api.example.com/v1/x")
    response = httpx2.Response(status, request=request, json={"error": {"message": body}})
    return openai.APIStatusError(body, response=response, body={"error": {"message": body}})


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_model_error_text_never_contains_the_key(status):
    exc = make_status_error(status, body="Incorrect API key provided: sk-test-secret")
    config = configure_app({"LLM_PROVIDER": "openai", "LLM_MODEL": "m", "LLM_API_KEY": "sk-test-secret"})
    assert "sk-test-secret" not in model_error_text(exc, config)


def test_unlisted_status_is_shown_with_its_message():
    assert explain_model_error(make_status_error(500, "upstream down"), "openai") == "HTTP 500: upstream down"


@pytest.mark.parametrize(
    "status, text",
    [(401, "az login"), (403, "private endpoint"), (404, "LLM_MODEL"), (429, "quota")],
)
def test_foundry_hints_by_status(status, text):
    assert text in explain_model_error(make_status_error(status), "foundry")


def test_foundry_401_hint_mentions_the_role():
    # Foundry reports a missing data-plane role as 401, not 403 (Microsoft Learn: troubleshoot-errors).
    assert "Cognitive Services OpenAI User" in explain_model_error(make_status_error(401), "foundry")


@pytest.mark.parametrize(
    "status, text",
    [
        (401, "the API key was rejected (check LLM_API_KEY_FILE)"),
        (404, "LLM_MODEL is not a model this endpoint serves, or LLM_ENDPOINT is wrong"),
        (429, "rate limited"),
    ],
)
def test_other_providers_hints_by_status(status, text):
    assert text in explain_model_error(make_status_error(status), "anthropic")


def test_connection_and_timeout_hints():
    request = openai.APIConnectionError(request=None)
    assert "cannot reach LLM_ENDPOINT" in explain_model_error(request, "ollama")
    assert "docs/model-providers.md" in explain_model_error(request, "ollama")
    assert "docs/foundry-setup.md" not in explain_model_error(request, "ollama")
    assert "docs/foundry-setup.md, section Networking" in explain_model_error(request, "foundry")
    timeout = openai.APITimeoutError(request=None)
    assert "LLM_TIMEOUT_SECONDS" in explain_model_error(timeout, "ollama")


def test_truncated_hint_names_the_output_limit_only_on_the_messages_api():
    # LLM_MAX_OUTPUT_TOKENS is sent only on the Messages API (anthropic).
    text = explain_model_error(ModelOutputTruncated("cut"), "anthropic")
    assert "LLM_MAX_OUTPUT_TOKENS" in text and "LLM_REASONING_EFFORT" in text
    for provider in ("foundry", "openai", "ollama", "openai-compatible"):
        text = explain_model_error(ModelOutputTruncated("cut"), provider)
        assert "LLM_MAX_OUTPUT_TOKENS" not in text
        assert "LLM_REASONING_EFFORT" in text and "shorten the question" in text


def test_token_error_hint():
    # azure-core comes with the optional azure extra.
    ClientAuthenticationError = pytest.importorskip("azure.core.exceptions").ClientAuthenticationError
    assert "Entra ID token" in explain_model_error(ClientAuthenticationError("no credential"), "foundry")


def test_explain_unknown_error():
    assert explain_model_error(RuntimeError("x")) == "RuntimeError: x"


def test_model_errors_cover_the_sdks_and_truncation():
    assert issubclass(openai.OpenAIError, MODEL_ERRORS[0]) or openai.OpenAIError in MODEL_ERRORS
    assert ModelOutputTruncated in MODEL_ERRORS
    ClientAuthenticationError = pytest.importorskip("azure.core.exceptions").ClientAuthenticationError
    assert ClientAuthenticationError in MODEL_ERRORS


def test_model_call_failed_is_reported_with_its_text_and_redacted():
    exc = ModelCallFailed("the model call failed: server_error: bad key sk-test-secret")
    assert ModelCallFailed in MODEL_ERRORS
    config = configure_app({"LLM_PROVIDER": "openai", "LLM_MODEL": "m", "LLM_API_KEY": "sk-test-secret"})
    text = model_error_text(exc, config)
    assert text == "the model call failed: server_error: bad key [redacted]"


def _config(provider, endpoint, **env):
    return configure_app({"LLM_PROVIDER": provider, "LLM_ENDPOINT": endpoint, "LLM_MODEL": "m", **env})


FOUNDRY_HOST = "https://my-resource.services.ai.azure.com"


def test_connection_hint_for_a_foundry_endpoint_points_at_the_networking_docs():
    config = _config("anthropic", FOUNDRY_HOST, LLM_API_KEY="sk-test-key")
    assert "docs/foundry-setup.md" in model_error_text(openai.APIConnectionError(request=None), config)


def test_claude_on_foundry_with_entra_gets_the_foundry_role_hint():
    config = _config("anthropic", FOUNDRY_HOST, LLM_AUTH="entra")
    text = model_error_text(make_status_error(401), config)
    assert "check LLM_API_KEY_FILE" not in text
    assert "Entra ID token was rejected" in text
    assert "'Cognitive Services User'" in text and "'Cognitive Services OpenAI User'" in text


def test_foundry_with_a_key_points_at_the_key_file():
    config = _config("foundry", FOUNDRY_HOST, LLM_API_KEY="sk-test-key")
    text = model_error_text(make_status_error(401), config)
    assert "check LLM_API_KEY_FILE" in text and "az login" not in text


def test_partner_model_on_foundry_via_openai_compatible_gets_foundry_hints():
    config = _config("openai-compatible", FOUNDRY_HOST + "/models", LLM_AUTH="entra")
    assert "private endpoint" in model_error_text(make_status_error(403), config)


def test_foundry_403_hint_mentions_both_roles():
    text = explain_model_error(make_status_error(403), "foundry")
    assert "'Cognitive Services User'" in text and "'Cognitive Services OpenAI User'" in text


def test_invalid_sign_in_settings_fall_back_to_a_neutral_401_hint():
    # LLM_AUTH=entra with a key is invalid; the hint must still be produced.
    config = _config("foundry", FOUNDRY_HOST, LLM_AUTH="entra", LLM_API_KEY="sk-test-key")
    text = model_error_text(make_status_error(401), config)
    assert "az login" in text and "LLM_API_KEY_FILE" in text


def test_non_foundry_anthropic_keeps_the_plain_key_hint():
    config = _config("anthropic", "", LLM_API_KEY="sk-test-key")
    assert "the API key was rejected (check LLM_API_KEY_FILE)" in model_error_text(make_status_error(401), config)
