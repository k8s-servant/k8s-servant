# OpenAI-compatible servers: vLLM, LiteLLM, Gemini and others

**Choose it when** your server speaks the OpenAI Chat Completions API and is not one of
the other providers: self-hosted vLLM or LM Studio, a LiteLLM gateway in front of many
models, Google Gemini's OpenAI-compatible endpoint, or a partner model on Foundry that
only offers Chat Completions. `LLM_ENDPOINT` is the full base URL including its path
(usually ending in `/v1`). A key is sent only if you configure one. The reasoning effort
is not sent unless you set `LLM_REASONING_EFFORT`, because the model behind the endpoint
is unknown and many reject it. The model must support tool calling.

Cluster base: [`examples/values-minimal.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-minimal.yaml) is exactly
this provider with an API key and a Teams webhook:

```yaml
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: openai-compatible
  LLM_ENDPOINT: https://api.example.com/v1
  LLM_MODEL: my-model
  LLM_AUTH: api-key
llmApiKey:
  existingSecret: llm-api-key
```

Create the key Secret from a file named `api-key`:
`kubectl -n k8s-servant create secret generic llm-api-key --from-file=./api-key`.
For an endpoint without a key, drop `LLM_AUTH` and `llmApiKey`. Worked examples:

**vLLM** (in the cluster, no key):

```bash
LLM_PROVIDER=openai-compatible
LLM_ENDPOINT=http://vllm.inference.svc:8000/v1
LLM_MODEL=Qwen/Qwen3-32B
```

Start vLLM with tool calling enabled (`--enable-auto-tool-choice` and a
`--tool-call-parser` for the model); without it the agent gets no tool calls.

**LiteLLM gateway** (with a virtual key):

```bash
LLM_PROVIDER=openai-compatible
LLM_ENDPOINT=https://litellm.example.com/v1
LLM_MODEL=gpt-5            # a model name configured in LiteLLM
LLM_API_KEY_FILE=/path/to/litellm-key
```

**Gemini**:

```bash
LLM_PROVIDER=openai-compatible
LLM_ENDPOINT=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-2.5-pro
LLM_API_KEY_FILE=/path/to/gemini-key
```

**Partner model on Foundry** that offers only Chat Completions: use
`LLM_ENDPOINT=https://my-foundry.services.ai.azure.com/openai/v1/` (or your resource's
address) and `LLM_AUTH=entra`; Entra ID is accepted because the endpoint is a Foundry
address.

> [!WARNING]
> As with Ollama: an in-cluster `http` endpoint is allowed, but a key sent to it travels
> **unencrypted inside the cluster network**. Use https, or no key.

Expected `doctor` model lines:

```
model: openai-compatible / my-model via Chat Completions API, API key (file /var/run/secrets/llm/api-key)
model: openai-compatible / Qwen/Qwen3-32B via Chat Completions API, no key
```
