# Anthropic

**Choose it when** you want Claude directly from Anthropic, on a laptop or in any cluster,
with prompt caching (the large system prompt is repeated on every run, so caching saves
cost and time). It uses the Messages API.

Laptop `.env`:

```bash
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-5-5
LLM_API_KEY_FILE=/path/to/anthropic-api-key   # or LLM_API_KEY=... for a quick test
```

No `LLM_ENDPOINT` is needed (it defaults to `https://api.anthropic.com`).

Cluster: [`examples/values-anthropic.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-anthropic.yaml). Create the
Secret exactly as for [OpenAI](openai.md) (a file named `api-key`, secret
`k8s-servant-llm`), then:

```yaml
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: anthropic
  LLM_MODEL: claude-sonnet-5-5
  LLM_AUTH: api-key
llmApiKey:
  existingSecret: k8s-servant-llm
```

Expected `doctor` model line:

```
model: anthropic / claude-sonnet-5-5 via Messages API, API key (file /var/run/secrets/llm/api-key)
```

Reasoning effort maps onto Anthropic's effort levels (`minimal` is sent as `low` with a warning
in the log, `none` switches thinking off). If a reply hits the output limit the run fails with a message
naming `LLM_MAX_OUTPUT_TOKENS`.
