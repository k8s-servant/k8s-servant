import pytest
from azure.core.exceptions import ClientAuthenticationError

from k8s_servant.llm import build_client, explain_model_error, normalize_endpoint
from k8s_servant.llm.types import ModelOutputTruncated


@pytest.mark.parametrize(
    "raw",
    [
        "https://res.openai.azure.com",
        "https://res.openai.azure.com/",
        "https://res.openai.azure.com/openai/v1",
        "https://res.openai.azure.com/openai/v1/",
        "  https://res.openai.azure.com/  ",
    ],
)
def test_normalize_endpoint(raw):
    assert normalize_endpoint(raw) == "https://res.openai.azure.com/openai/v1/"


def test_normalize_endpoint_rejects_http():
    with pytest.raises(ValueError, match="https://"):
        normalize_endpoint("http://res.openai.azure.com")


@pytest.mark.parametrize(
    "raw, expected",
    [
        # Any other OpenAI-compatible endpoint: a URL with a path is used as given.
        ("https://api.openai.com/v1", "https://api.openai.com/v1/"),
        ("https://llm.example.com/proxy/v1/", "https://llm.example.com/proxy/v1/"),
        # Plain http only for a model on the same machine or pod (e.g. a local server).
        ("http://localhost:11434/v1", "http://localhost:11434/v1/"),
        ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1/"),
    ],
)
def test_normalize_endpoint_keeps_other_openai_compatible_urls(raw, expected):
    assert normalize_endpoint(raw) == expected


def test_normalize_endpoint_rejects_http_to_a_remote_host():
    with pytest.raises(ValueError, match="https://"):
        normalize_endpoint("http://llm.example.com/v1")


def test_build_client_with_api_key():
    config = {
        "foundry_api_key": "k",
        "foundry_endpoint": "https://res.openai.azure.com",
        "foundry_timeout_seconds": 10,
        "foundry_token_scope": "https://ai.azure.com/.default",
    }
    client = build_client(config)
    assert str(client.base_url) == "https://res.openai.azure.com/openai/v1/"


class _HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize(
    "status, text",
    [(401, "az login"), (403, "private endpoint"), (404, "FOUNDRY_DEPLOYMENT"), (429, "quota")],
)
def test_explain_model_error_by_status(status, text):
    assert text in explain_model_error(_HttpError(status))


def test_explain_token_error():
    assert "Entra ID token" in explain_model_error(ClientAuthenticationError("no credential"))


def test_explain_unknown_error():
    assert explain_model_error(RuntimeError("x")) == "RuntimeError: x"


def test_401_hint_mentions_missing_role():
    # Foundry reports a missing data-plane role as 401 "PermissionDenied ... lacks
    # the required data action", not 403 (Microsoft Learn: troubleshoot-errors).
    assert "Cognitive Services OpenAI User" in explain_model_error(_HttpError(401))


def test_explain_truncated_reply():
    assert "output limit" in explain_model_error(ModelOutputTruncated("cut"))
