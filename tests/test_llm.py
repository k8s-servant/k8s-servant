import pytest
from azure.core.exceptions import ClientAuthenticationError

from k8s_servant.llm import build_client, explain_model_error
from k8s_servant.llm.types import ModelOutputTruncated


def test_build_client_with_api_key():
    config = {
        "llm_api_key": "k",
        "llm_provider": "foundry",
        "llm_endpoint": "https://res.openai.azure.com",
        "llm_timeout_seconds": 10,
        "llm_token_scope": "https://ai.azure.com/.default",
    }
    client = build_client(config)
    assert str(client.base_url) == "https://res.openai.azure.com/openai/v1/"


class _HttpError(Exception):
    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


@pytest.mark.parametrize(
    "status, text",
    [(401, "az login"), (403, "private endpoint"), (404, "LLM_MODEL"), (429, "quota")],
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
