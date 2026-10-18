import pytest

from k8s_servant.llm.providers import OLD_SETTINGS, PROVIDERS, is_foundry_endpoint, normalize_endpoint


@pytest.mark.parametrize(
    "host,expected",
    [
        ("my-foundry.openai.azure.com", True),
        ("my-foundry.services.ai.azure.com", True),
        ("my-foundry.cognitiveservices.azure.com", True),
        ("openai.azure.com.example.com", False),
        ("api.anthropic.com", False),
    ],
)
def test_is_foundry_endpoint(host, expected):
    assert is_foundry_endpoint(f"https://{host}/") is expected


@pytest.mark.parametrize(
    "provider,url,expected",
    [
        ("foundry", "https://my-foundry.openai.azure.com", "https://my-foundry.openai.azure.com/openai/v1/"),
        (
            "foundry",
            "  https://my-foundry.openai.azure.com/openai/v1  ",
            "https://my-foundry.openai.azure.com/openai/v1/",
        ),
        (
            "anthropic",
            "https://my-foundry.services.ai.azure.com",
            "https://my-foundry.services.ai.azure.com/anthropic/",
        ),
        ("anthropic", "https://api.anthropic.com", "https://api.anthropic.com/"),
        ("openai", "https://api.openai.com/v1", "https://api.openai.com/v1/"),
        ("ollama", "http://localhost:11434/v1", "http://localhost:11434/v1/"),
        ("ollama", "http://ollama.ollama.svc:11434/v1", "http://ollama.ollama.svc:11434/v1/"),
        ("openai-compatible", "http://vllm:8000/v1", "http://vllm:8000/v1/"),
        ("openai-compatible", "http://vllm.ai.svc.cluster.local:8000/v1", "http://vllm.ai.svc.cluster.local:8000/v1/"),
        ("openai-compatible", "https://llm.example.com/proxy/v1/", "https://llm.example.com/proxy/v1/"),
    ],
)
def test_normalize_endpoint(provider, url, expected):
    assert normalize_endpoint(provider, url) == expected


@pytest.mark.parametrize(
    "provider,url",
    [
        ("foundry", "http://vllm:8000/v1"),
        ("foundry", "http://res.openai.azure.com"),
        ("openai-compatible", "http://models.example.com/v1"),
        ("anthropic", "http://localhost.example.com"),
    ],
)
def test_normalize_endpoint_rejects_remote_http(provider, url):
    with pytest.raises(ValueError, match="LLM_ENDPOINT"):
        normalize_endpoint(provider, url)


def test_every_old_setting_maps_to_an_llm_name():
    assert len(OLD_SETTINGS) == 7
    assert all(old.startswith("FOUNDRY_") and new.startswith("LLM_") for old, new in OLD_SETTINGS.items())


def test_provider_table_is_keyed_by_name():
    assert all(key == provider.name for key, provider in PROVIDERS.items())
