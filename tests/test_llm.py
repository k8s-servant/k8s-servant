from types import SimpleNamespace

import pytest
from azure.core.exceptions import ClientAuthenticationError

from k8s_servant.llm import FoundryChat, build_chat, build_client, explain_model_error, normalize_endpoint


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


class _FakeCompletions:
    """Records the kwargs of chat.completions.create and returns a canned reply."""

    def __init__(self):
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message="reply")])


async def test_foundry_chat_sends_tools_only_when_present():
    completions = _FakeCompletions()
    chat = FoundryChat(SimpleNamespace(chat=SimpleNamespace(completions=completions)), "my-deployment")
    assert await chat.complete([{"role": "user", "content": "hi"}], []) == "reply"
    assert completions.kwargs == {"model": "my-deployment", "messages": [{"role": "user", "content": "hi"}]}
    await chat.complete([], [{"type": "function"}])
    assert completions.kwargs["tools"] == [{"type": "function"}]


async def test_foundry_chat_sends_reasoning_effort_when_set():
    completions = _FakeCompletions()
    chat = FoundryChat(SimpleNamespace(chat=SimpleNamespace(completions=completions)), "d", reasoning_effort="medium")
    await chat.complete([], [])
    assert completions.kwargs["reasoning_effort"] == "medium"


async def test_foundry_chat_logs_and_counts_tokens(caplog):
    # Needed to size the deployment: the first deep health check hit the 50k
    # tokens-per-minute limit and there was no way to see how many it used.
    usage = SimpleNamespace(
        prompt_tokens=1200,
        completion_tokens=300,
        prompt_tokens_details=SimpleNamespace(cached_tokens=1024),
        completion_tokens_details=SimpleNamespace(reasoning_tokens=200),
    )

    class Completions:
        async def create(self, **kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message="reply")], usage=usage)

    chat = FoundryChat(SimpleNamespace(chat=SimpleNamespace(completions=Completions())), "d")
    caplog.set_level("INFO")
    await chat.complete([], [])
    await chat.complete([], [])
    # Cached prompt tokens are billed at a discount; they show the real cost.
    assert "1200 prompt (1024 cached) + 300 completion tokens (200 reasoning)" in caplog.text
    assert chat.total_tokens == 3000


def test_build_chat_uses_the_configured_deployment_and_effort():
    config = {
        "foundry_endpoint": "https://x.openai.azure.com",
        "foundry_api_key": "k",
        "foundry_timeout_seconds": 5,
        "foundry_deployment": "dep",
        "foundry_reasoning_effort": "low",
    }
    chat = build_chat(config)
    assert (chat.deployment, chat.reasoning_effort) == ("dep", "low")


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
