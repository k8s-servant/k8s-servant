# Ollama (laptop and in-cluster)

**Choose it when** the cluster data must not leave your network, or you want to try the
agent for free. Quality depends on the model: it must support tool calling (for example
`qwen3`), and small models find fewer problems than the hosted ones. It uses the Chat
Completions API, needs no key, and does not send a reasoning effort.

**Laptop** (Ollama running locally):

```bash
ollama pull qwen3
```

```bash
LLM_PROVIDER=ollama
LLM_MODEL=qwen3
# LLM_ENDPOINT defaults to http://localhost:11434/v1
```

**In the cluster**: run Ollama as a Deployment with a Service (any way you like; the
model must be pulled into it), then use
[`examples/values-ollama.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-ollama.yaml):

```yaml
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: ollama
  LLM_ENDPOINT: http://ollama.ollama.svc:11434/v1
  LLM_MODEL: qwen3
```

Plain `http` is accepted for `localhost` and for in-cluster hosts (a name without dots,
`*.svc` or `*.svc.cluster.local`), by design: a model in the same cluster is the main
use case. Anything else must be https.

> [!WARNING]
> Over plain `http` nothing is encrypted. If you do set an API key for such an endpoint
> (for example behind a proxy that checks it), the key travels **unencrypted inside the
> cluster network**. Prefer an https endpoint, or use no key. The cluster data the
> agent sends (object names, events, log lines) is unencrypted on that hop too.
> Entra ID tokens never go over http: Foundry hosts are always https.

Expected `doctor` model line:

```
model: ollama / qwen3 via Chat Completions API, no key
```
