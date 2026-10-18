import builtins

import pytest

from k8s_servant.llm import build_chat, describe_model
from k8s_servant.llm.chat_completions import ChatCompletionsChat
from k8s_servant.llm.responses import ResponsesChat


def _config(provider, endpoint, **extra):
    config = {
        "llm_provider": provider,
        "llm_endpoint": endpoint,
        "llm_model": "my-model",
        "llm_api_key": "",
        "llm_api_key_source": "",
        "llm_auth": "auto",
        "llm_token_scope": "https://ai.azure.com/.default",
        "llm_reasoning_effort": "",
        "llm_timeout_seconds": 5,
        "workload_identity": False,
    }
    config.update(extra)
    return config


@pytest.mark.parametrize(
    "provider,endpoint,cls",
    [
        ("ollama", "http://localhost:11434/v1", ChatCompletionsChat),
        ("openai-compatible", "https://llm.example.com/v1", ChatCompletionsChat),
        ("foundry", "https://my-foundry.openai.azure.com", ResponsesChat),
        ("openai", "https://api.openai.com/v1", ResponsesChat),
    ],
)
def test_build_chat_picks_the_client(provider, endpoint, cls):
    chat = build_chat(_config(provider, endpoint, llm_api_key="k"))
    assert isinstance(chat, cls)
    assert chat.model == "my-model"


def test_chat_completions_client_uses_the_configured_base_url_and_effort():
    chat = build_chat(_config("openai-compatible", "https://llm.example.com/v1", llm_reasoning_effort="low"))
    assert str(chat.client.base_url) == "https://llm.example.com/v1/"
    assert chat.reasoning_effort == "low"


def test_clients_still_to_come_say_which_is_missing():
    with pytest.raises(NotImplementedError, match="not implemented yet"):
        build_chat(_config("anthropic", "https://api.example.com/v1", llm_api_key="k"))


def test_build_chat_rejects_an_invalid_auth_combination():
    with pytest.raises(ValueError, match="LLM_API_KEY_FILE"):
        build_chat(_config("openai", "https://api.openai.com/v1"))


def test_describe_model_examples():
    assert (
        describe_model(
            _config("foundry", "https://x.openai.azure.com", llm_model="my-deployment", workload_identity=True)
        )
        == "foundry / my-deployment via Responses API, Entra ID (Workload Identity)"
    )
    assert (
        describe_model(
            _config(
                "anthropic",
                "https://api.anthropic.com",
                llm_model="claude-sonnet-5-5",
                llm_api_key="k",
                llm_api_key_source="file /var/run/secrets/llm/api-key",
            )
        )
        == "anthropic / claude-sonnet-5-5 via Messages API, API key (file /var/run/secrets/llm/api-key)"
    )
    assert (
        describe_model(_config("ollama", "http://localhost:11434/v1", llm_model="qwen3"))
        == "ollama / qwen3 via Chat Completions API, no key"
    )


def test_entra_without_azure_identity_says_install_the_extra(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("azure"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    config = _config("openai-compatible", "https://x.openai.azure.com/openai/v1")
    with pytest.raises(ValueError, match=r"k8s-servant\[azure\]"):
        build_chat(config)


def test_no_entra_token_is_minted_for_a_non_foundry_endpoint(monkeypatch):
    # A token must never be sent to a base URL that is not an Azure resource.
    def forbidden(scope):
        raise AssertionError("entra_token_provider must not be called")

    monkeypatch.setattr("k8s_servant.llm.chat_completions.entra_token_provider", forbidden)
    chat = build_chat(_config("openai-compatible", "https://llm.example.com/v1"))
    assert chat.client.api_key == "unused"


def test_entra_is_enforced_where_the_client_is_built(monkeypatch):
    called = []
    monkeypatch.setattr("k8s_servant.llm.chat_completions.entra_token_provider", lambda scope: called.append(scope))
    with pytest.raises(ValueError, match="LLM_AUTH=entra"):
        build_chat(_config("openai-compatible", "https://llm.example.com/v1", llm_auth="entra"))
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        build_chat(
            _config("openai-compatible", "https://x.openai.azure.com/openai/v1", llm_auth="entra", llm_api_key="k")
        )
    with pytest.raises(ValueError, match="LLM_AUTH=api-key"):
        build_chat(_config("openai-compatible", "https://x.openai.azure.com/openai/v1", llm_auth="api-key"))
    assert called == []


def test_entra_on_a_foundry_endpoint_uses_the_token_provider(monkeypatch):
    async def token():
        return "tok"

    monkeypatch.setattr("k8s_servant.llm.chat_completions.entra_token_provider", lambda scope: token)
    chat = build_chat(_config("openai-compatible", "https://x.openai.azure.com/openai/v1"))
    assert chat.client._api_key_provider is token
