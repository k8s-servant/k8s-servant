# Configuration

All settings are environment variables. Locally they come from `.env`; in the cluster
from the Helm chart (`appConfig` in the values, rendered into a ConfigMap). A test keeps these tables in sync with
`k8s_servant/config.py`.

## Cluster and model

| Variable | Default | Meaning |
|---|---|---|
| `CLUSTER_NAME` | `unknown-cluster` | Friendly cluster name used in answers, reports and Teams cards. |
| `LLM_PROVIDER` | *(required)* | Who serves the model: `foundry`, `openai`, `anthropic`, `ollama` or `openai-compatible`. It decides the wire API, the default endpoint and the default reasoning effort. |
| `LLM_ENDPOINT` | provider default | Base URL. Required for `foundry` (the resource endpoint, e.g. `https://<name>.openai.azure.com/`; `/openai/v1/` is appended automatically) and `openai-compatible`. Defaults: `https://api.openai.com/v1`, `https://api.anthropic.com`, `http://localhost:11434/v1` (ollama). Plain `http` is accepted for localhost and, for `ollama` and `openai-compatible`, in-cluster hosts. |
| `LLM_MODEL` | *(required)* | Model name. For `foundry` the **deployment** name. |
| `LLM_API_KEY` | *(empty)* | API key, for providers that need one. Prefer `LLM_API_KEY_FILE`. Leave it empty for Microsoft Foundry with Entra ID and for servers without a key (Ollama). |
| `LLM_API_KEY_FILE` | *(empty)* | File containing the API key (the mounted Secret in the cluster, set by the chart from `llmApiKey.existingSecret`); wins over `LLM_API_KEY`. An unreadable or empty file stops the agent with an error. |
| `LLM_AUTH` | `auto` | `auto`: the API key when one is set; otherwise Entra ID for Microsoft Foundry endpoints, no sign-in for `ollama` and `openai-compatible`, and an error for the other providers. `entra` or `api-key` force one method. |
| `LLM_TOKEN_SCOPE` | `https://ai.azure.com/.default` | Scope requested for the Entra ID token. |
| `LLM_TIMEOUT_SECONDS` | `120` | Timeout of one model request (the SDK retries up to 3 times; retries are logged). |
| `LLM_MAX_OUTPUT_TOKENS` | `32000` | Output token limit of one model reply (reasoning tokens count against it). |
| `LLM_REASONING_EFFORT` | `medium` for `foundry`, `openai`, `anthropic`; empty otherwise | How hard a reasoning model thinks: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. gpt-5.4-mini does not reason at all unless asked. Empty = not sent (for models that reject it). |
| `AZURE_FEDERATED_TOKEN_FILE` | *(set by the platform)* | Injected by the Azure Workload Identity webhook; its presence tells the agent the pod authenticates with Workload Identity (used to explain Entra ID failures). Do not set it by hand. |

## Reading the cluster

| Variable | Default | Meaning |
|---|---|---|
| `MCP_URL` | `http://localhost:8080/mcp` | kubernetes-mcp-server endpoint. Empty = MCP disabled, kubectl only. |
| `MCP_TIMEOUT_SECONDS` | `60` | Timeout of one MCP tool call; a timed-out call reaches the model as an `ERROR:` result. |
| `KUBECTL_PATH` | `kubectl` | kubectl binary (fallback tool and summary tools). |
| `KUBECTL_TIMEOUT_SECONDS` | `30` | Timeout of one kubectl call (also used by `cluster_problems` and `warning_events`). |
| `EVENTS_EXCLUDE_REASONS` | `PolicyViolation` | Warning event reasons `warning_events` hides unless asked (comma-separated). Policy scanners such as Kyverno emit thousands of these. Empty = hide nothing. |

## The agent loop

| Variable | Default | Meaning |
|---|---|---|
| `MAX_ITERATIONS` | `25` | Maximum model round-trips per question. |
| `TOOL_OUTPUT_MAX_CHARS` | `8000` | Tool output longer than this is cut (head and tail kept). Every later model call resends it, so it drives the token cost. |

## Knowledge and memory

| Variable | Default | Meaning |
|---|---|---|
| `KNOWLEDGE_DIR` | chart folder `charts/k8s-servant/knowledge` | Folder with `environment.md`, `cluster.md` and skills. |
| `DATA_DIR` | `.data` | Memory folder: findings, run history, learned notes ([memory.md](memory.md)). `/data` in the cluster. |
| `NOTES_MAX_CHARS` | `8000` | How much of the newest learned notes goes into the system prompt. |

## Prometheus and Alertmanager (optional)

| Variable | Default | Meaning |
|---|---|---|
| `PROMETHEUS_URL` | *(empty)* | Prometheus base URL; enables the PromQL tools and "alerts that stopped overnight" in the morning report. |
| `ALERTMANAGER_URL` | *(empty)* | Alertmanager base URL; enables the alerts tool and "alerts firing now". |
| `PROMETHEUS_TIMEOUT_SECONDS` | `30` | Timeout of one Prometheus or Alertmanager request. |
| `ALERTS_IGNORE_NAMES` | `Watchdog,InfoInhibitor` | Always-firing alerts, left out of the morning report and of the "anything changed?" comparison. Empty = use all. |

## MS Teams

| Variable | Default | Meaning |
|---|---|---|
| `TEAMS_WEBHOOK_URL` | *(empty)* | Teams Workflows webhook URL (a credential). |
| `TEAMS_WEBHOOK_URL_FILE` | *(empty)* | File containing the webhook URL (the mounted Secret in the cluster); wins over `TEAMS_WEBHOOK_URL`. |
| `TEAMS_POST_MODE` | `changes` | `changes`: post a check only when something is new or resolved; `always`; `off`. The morning report is posted unless `off`. |

## Scheduling (`k8s-servant serve`)

| Variable | Default | Meaning |
|---|---|---|
| `CHECK_INTERVAL_MINUTES` | `60` | Minutes between scheduled health checks. |
| `CHECK_SKIP_UNCHANGED` | `true` | Scheduled checks skip the model when problems and firing alerts are identical to the last full check (saves ~150k tokens per check). Manual `check` and the morning report always run in full. |
| `CHECK_FULL_INTERVAL_HOURS` | `6` | Run a full check at least this often even when nothing changed. |
| `JOB_TIMEOUT_MINUTES` | `30` | A scheduled check or morning report running longer than this is cancelled. |
| `MORNING_REPORT_ENABLED` | `true` | Post the morning report. |
| `MORNING_REPORT_TIME` | `08:00` | Local time of the morning report. |
| `MORNING_REPORT_TIMEZONE` | `UTC` | IANA time zone of `MORNING_REPORT_TIME`, e.g. `Europe/Berlin` (summer time handled). |
| `MORNING_REPORT_WEEKDAYS_ONLY` | `true` | Skip Saturdays and Sundays (Monday's report then covers the weekend). |
| `SCAN_BEFORE_REPORT` | `true` | Before each morning report, scan the cluster again (no model, no tokens) and add "Since the last scan" with what changed. See [onboarding.md](onboarding.md#re-scan-before-the-morning-report). |
| `HEARTBEAT_FILE` | *(empty)* | File touched every scheduler loop for a liveness probe (`/tmp/heartbeat` in the cluster). |

## Logging

| Variable | Default | Meaning |
|---|---|---|
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO` or `WARNING`. Logs go to stderr; answers go to stdout. |

## Secrets: local vs cluster

`.env` is only a convenience for running the CLI on a laptop: it collects your local
settings so you can load them into your shell with `set -a; source .env; set +a`. The
agent itself only reads environment variables. In the cluster there is no `.env` at
all.

Almost nothing in `.env` is secret:

| Value | Secret? | Local (laptop) | Cluster |
|---|---|---|---|
| Endpoints, names, URLs of Prometheus etc., timeouts | No | `.env` | ConfigMap rendered by the Helm chart |
| **The model's API key** (OpenAI, Anthropic, most hosted providers) | **Yes** | a file only you can read, named in `LLM_API_KEY_FILE` ([Quickstart](getting-started.md)) | a Kubernetes Secret you create, named in `llmApiKey.existingSecret`, mounted as a file (`LLM_API_KEY_FILE`) |
| **Model access without a key** | — | Ollama: nothing. Microsoft Foundry with Entra ID: your `az login` session | Ollama in the cluster: nothing. Foundry on AKS: **Workload Identity**, the pod's ServiceAccount federated with a managed identity that has *Cognitive Services OpenAI User* on the Foundry resource (*Cognitive Services User* for Claude and other non-OpenAI models) |
| **`TEAMS_WEBHOOK_URL`** | **Yes**: anyone with it can post to the channel | load it from your secret store into your shell (below) | A Kubernetes Secret you create any way you like (kubectl, External Secrets, Sealed Secrets, ...), named in `teamsWebhook.existingSecret`, mounted as a file and read through `TEAMS_WEBHOOK_URL_FILE`. `examples/values-azure.yaml` syncs it from Azure Key Vault with External Secrets |

In the Azure example, one managed identity does both jobs: it calls the model and
reads the webhook secret from Key Vault (for External Secrets).
`scripts/setup-workload-identity.sh` creates it
([deployment.md](deployment.md#1-workload-identity-aks)). The chart itself
never creates Secrets; see
[Credentials](deployment.md#credentials-bring-your-own-secrets).

**What protects `.env` locally.** It is listed in `.gitignore`, so git never commits it,
and neither the agent's memory (`.data/`). It is still a plain-text file on your disk,
readable by anything that runs as you (backups, sync tools). So keep secrets out of it:

```bash
# Recommended: load the webhook URL from Key Vault into this shell session only.
set -a; source .env; set +a
export TEAMS_WEBHOOK_URL=$(az keyvault secret show --vault-name my-keyvault \
  --name teams-webhook-url --query value -o tsv)

# Alternative: a private file outside the repository, read the same way as in the pod.
mkdir -p ~/.config/k8s-servant && chmod 700 ~/.config/k8s-servant
printf '%s' '<webhook-url>' > ~/.config/k8s-servant/webhook-url && chmod 600 ~/.config/k8s-servant/webhook-url
export TEAMS_WEBHOOK_URL_FILE=~/.config/k8s-servant/webhook-url
```

Do not paste secrets directly into commands you type: they end up in your shell
history. The agent never writes the webhook URL to its logs, history or Teams cards.
