import openai
import pytest
from azure.core.exceptions import ClientAuthenticationError

from k8s_servant.llm.errors import MODEL_ERRORS, explain_model_error, model_error_text
from k8s_servant.llm.types import ModelOutputTruncated


def make_status_error(status, body="boom"):
    """An openai.APIStatusError of the given status, as the SDK raises it."""
    import httpx2

    request = httpx2.Request("POST", "https://api.example.com/v1/x")
    response = httpx2.Response(status, request=request, json={"error": {"message": body}})
    return openai.APIStatusError(body, response=response, body={"error": {"message": body}})


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_model_error_text_never_contains_the_key(status):
    exc = make_status_error(status, body="Incorrect API key provided: sk-test-secret")
    config = {"llm_provider": "openai", "llm_api_key": "sk-test-secret"}
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
    timeout = openai.APITimeoutError(request=None)
    assert "LLM_TIMEOUT_SECONDS" in explain_model_error(timeout, "ollama")


def test_truncated_hint_names_the_settings():
    text = explain_model_error(ModelOutputTruncated("cut"), "openai")
    assert "LLM_MAX_OUTPUT_TOKENS" in text and "LLM_REASONING_EFFORT" in text


def test_token_error_hint():
    assert "Entra ID token" in explain_model_error(ClientAuthenticationError("no credential"), "foundry")


def test_explain_unknown_error():
    assert explain_model_error(RuntimeError("x")) == "RuntimeError: x"


def test_model_errors_cover_the_sdks_and_truncation():
    assert issubclass(openai.OpenAIError, MODEL_ERRORS[0]) or openai.OpenAIError in MODEL_ERRORS
    assert ModelOutputTruncated in MODEL_ERRORS
    assert ClientAuthenticationError in MODEL_ERRORS
