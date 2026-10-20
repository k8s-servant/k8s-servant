# Getting started

Try it on a throwaway cluster with something broken in it, in about 10 minutes. You
need Python 3.12 or newer (`python3 --version`), `kubectl`, Docker and
[kind](https://kind.sigs.k8s.io/).

**1. Install**

```bash
git clone https://github.com/k8s-servant/k8s-servant.git && cd k8s-servant
python3 -m venv .venv
.venv/bin/pip install -e .
source .venv/bin/activate
```

`pip install -e .` is all a user needs. Developers add the test tools with
`'.[dev]'`; Azure AI Foundry with Entra ID sign-in also needs the `azure` extra, for
example `'.[dev,azure]'`.

**2. A cluster with something broken**

```bash
kind create cluster --name k8s-servant-demo    # kubectl now points at kind-k8s-servant-demo
kubectl create deployment web --image=nginx:1.27
kubectl create deployment api --image=busybox:1.36 -- \
  sh -c 'echo "connecting to db:5432"; sleep 2; echo "error: connection refused"; exit 1'
kubectl create deployment worker --image=nginx:9.99-does-not-exist
```

`web` is healthy, `api` crash-loops and `worker` cannot pull its image. To use one of
your own clusters instead, skip this step: the agent reads whichever cluster
`kubectl config current-context` names, and only reads.

**3. Choose a model**

*Option A: Ollama, free and local.* Install [Ollama](https://ollama.com/), then:

```bash
ollama pull qwen3                                 # about 5 GB
export LLM_PROVIDER=ollama LLM_MODEL=qwen3
```

Good enough to see how it works, not to rely on. On this demo, qwen3 found both broken
Deployments and wrote a correct health check, but it stopped after the first tool call
and did not read the crash logs. With Claude on the same demo, the agent read the logs,
the pod's events and the container command, and named the exact cause of both failures.

*Option B: an API key from Anthropic or OpenAI.* Keep the key in a file only you can
read, not on the command line (it would stay in your shell history):

```bash
mkdir -p ~/.config/k8s-servant && chmod 700 ~/.config/k8s-servant
nano ~/.config/k8s-servant/llm-api-key          # paste the key, save
chmod 600 ~/.config/k8s-servant/llm-api-key
export LLM_API_KEY_FILE=~/.config/k8s-servant/llm-api-key
export LLM_PROVIDER=anthropic LLM_MODEL=claude-sonnet-5-5   # or: LLM_PROVIDER=openai LLM_MODEL=gpt-5
```

Azure AI Foundry (Entra ID, no key), Claude on Foundry and any OpenAI-compatible server
have their own recipes in [model-providers.md](model-providers.md).

**4. Ask**

```bash
export CLUSTER_NAME=demo MCP_URL=     # empty MCP_URL: kubectl only (MCP server: see below)
k8s-servant doctor                    # OK for config, kubectl and model; WARN lines are optional extras
k8s-servant ask "Are there any unhealthy pods? Why?"
k8s-servant check                     # one health check, remembered in ./.data
k8s-servant findings                  # what is open now
```

On a laptop `doctor` warns that your identity can read Secrets: that is expected with
an admin kubeconfig. In the cluster the agent's own RBAC must say no.

**5. Clean up**

```bash
kind delete cluster --name k8s-servant-demo && rm -rf .data
```

## Going further

- **Keep your settings.** `cp .env.example .env`, fill it in (no secrets in it: see
  [Secrets: local vs cluster](configuration.md#secrets-local-vs-cluster)), then load it into each new
  shell with `set -a; source .env; set +a`.
- **The MCP server** (recommended; the agent falls back to kubectl without it). In its
  own terminal, with the binary from its GitHub releases or through npm:

  ```bash
  npx -y kubernetes-mcp-server@0.0.67 --config deploy/local/mcp-config.toml
  ```

  Then `unset MCP_URL` in the shell you use for the agent (step 4 set it to empty, which
  turns MCP off); unset, it defaults to `http://localhost:8080/mcp`.
- **Prometheus and Alertmanager**, through port-forwards in their own terminals (adjust
  namespace and service names to your monitoring stack), then set
  `PROMETHEUS_URL=http://localhost:9090` and `ALERTMANAGER_URL=http://localhost:9093`.
  Port-forwards drop now and then; restart them when `doctor` reports FAIL.

  ```bash
  kubectl -n monitoring port-forward svc/prometheus-operated 9090
  kubectl -n monitoring port-forward svc/alertmanager-operated 9093
  ```

- **Your platform's knowledge**, the same files the cluster uses:

  ```bash
  alias kss='k8s-servant --values examples/knowledge-values.yaml --values my-values.yaml'
  kss report           # the morning report, now
  kss check --post     # a health check, posted to Teams (see [MS Teams setup](teams-setup.md))
  kss serve            # the scheduler, as it runs in the cluster (Ctrl-C to stop)
  ```

`k8s-servant doctor` checks configuration, knowledge, memory, MCP, kubectl, RBAC,
Prometheus, Alertmanager, Teams and the model, one line each. Run it first when
something does not work, then see [troubleshooting.md](troubleshooting.md).
All commands: [cli.md](cli.md).
