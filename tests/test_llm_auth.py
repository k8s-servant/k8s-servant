import builtins
import logging

import pytest

from k8s_servant.llm.auth import ModelAuth, entra_token_provider, resolve_model_auth


def _config(provider, endpoint="", **extra):
    config = {
        "llm_provider": provider,
        "llm_endpoint": endpoint,
        "llm_api_key": "",
        "llm_api_key_source": "",
        "llm_auth": "auto",
        "workload_identity": False,
    }
    config.update(extra)
    return config


@pytest.mark.parametrize(
    "provider,endpoint",
    [
        ("anthropic", "https://my-foundry.services.ai.azure.com"),
        ("openai-compatible", "https://my-foundry.openai.azure.com/openai/v1"),
        ("foundry", "https://my-foundry.openai.azure.com"),
    ],
)
def test_auto_without_key_on_foundry_endpoints_is_entra(provider, endpoint):
    assert resolve_model_auth(_config(provider, endpoint)).method == "entra"


def test_entra_source_names_workload_identity_or_az_login():
    wi = resolve_model_auth(_config("foundry", "https://x.openai.azure.com", workload_identity=True))
    assert (wi.source, wi.label) == ("Workload Identity", "Entra ID (Workload Identity)")
    local = resolve_model_auth(_config("foundry", "https://x.openai.azure.com"))
    assert local.source == "az login / DefaultAzureCredential"


def test_entra_rejected_for_anthropic_direct():
    with pytest.raises(ValueError, match="LLM_AUTH=entra"):
        resolve_model_auth(_config("anthropic", "https://api.anthropic.com", llm_auth="entra"))


def test_entra_rejected_when_a_key_is_set():
    config = _config("foundry", "https://x.openai.azure.com", llm_auth="entra", llm_api_key="k")
    with pytest.raises(ValueError, match="LLM_API_KEY"):
        resolve_model_auth(config)


def test_entra_accepted_on_a_foundry_endpoint():
    config = _config("foundry", "https://x.openai.azure.com", llm_auth="entra")
    assert resolve_model_auth(config).method == "entra"


def test_api_key_mode_without_key_fails():
    with pytest.raises(ValueError, match="LLM_AUTH=api-key"):
        resolve_model_auth(_config("openai", "https://api.openai.com/v1", llm_auth="api-key"))


def test_openai_without_key_fails_under_auto():
    with pytest.raises(ValueError, match="LLM_API_KEY_FILE"):
        resolve_model_auth(_config("openai", "https://api.openai.com/v1"))


@pytest.mark.parametrize("provider", ["ollama", "openai-compatible"])
def test_ollama_and_openai_compatible_run_without_key(provider):
    auth = resolve_model_auth(_config(provider, "http://localhost:11434/v1"))
    assert auth == ModelAuth("none", "", "no key")


def test_auto_warns_when_workload_identity_and_key(caplog):
    config = _config(
        "foundry",
        "https://x.openai.azure.com",
        llm_api_key="sk-test-secret",
        llm_api_key_source="LLM_API_KEY",
        workload_identity=True,
    )
    with caplog.at_level(logging.WARNING):
        auth = resolve_model_auth(config)
    assert auth.method == "api-key"
    assert "Workload Identity" in caplog.text and "sk-test-secret" not in caplog.text
    assert "LLM_AUTH=entra" in auth.warning and "sk-test-secret" not in auth.warning


def test_no_warning_for_workload_identity_and_key_on_a_non_azure_provider(caplog):
    config = _config("anthropic", "https://api.anthropic.com", llm_api_key="k", workload_identity=True)
    with caplog.at_level(logging.WARNING):
        auth = resolve_model_auth(config)
    assert caplog.text == ""
    assert auth.warning == ""


def test_label_names_the_key_file_not_the_key(tmp_path):
    source = f"file {tmp_path}/api-key"
    auth = resolve_model_auth(
        _config("anthropic", "https://api.anthropic.com", llm_api_key="sk-x", llm_api_key_source=source)
    )
    assert auth.method == "api-key"
    assert auth.label == f"API key ({source})"
    assert "sk-x" not in auth.label + auth.source


def test_entra_token_provider_without_azure_identity_says_install_the_extra(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name.startswith("azure"):
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ValueError, match=r"k8s-servant\[azure\]"):
        entra_token_provider("https://ai.azure.com/.default")
