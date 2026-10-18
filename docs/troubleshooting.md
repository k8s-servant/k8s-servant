# Troubleshooting

Start with **`k8s-servant doctor`**: it checks every dependency, one line each, and makes
one small model call. Then look at the log (stderr; `--log-level DEBUG` shows full tool
outputs). Most entries below were met for real during the setup and the first runs
on a large test cluster.

## The model (Azure AI Foundry)

| Symptom | Cause | Fix |
|---|---|---|
| `HTTP 401` with a key (any provider) | The key was rejected: wrong key, wrong provider, or a stray newline | Check the file named by `LLM_API_KEY_FILE`; create the Secret with `--from-file=./api-key` |
| `LLM_AUTH=entra needs an Azure AI Foundry endpoint` | Entra ID is only used for Foundry hosts (`.openai.azure.com`, `.services.ai.azure.com`, `.cognitiveservices.azure.com`) | Use `LLM_AUTH=api-key` with a key, or fix `LLM_ENDPOINT` |
| `Entra ID sign-in needs azure-identity` | The `azure` extra is missing outside the image | `pip install 'k8s-servant[azure]'` |
| "the reply hit the output limit" | A reply was cut off by the limit; the run is recorded as failed | Raise `LLM_MAX_OUTPUT_TOKENS` (Messages API only) or lower `LLM_REASONING_EFFORT`; on the other APIs, also shorten the question |
| `HTTP 401` right after setup | The data-plane role takes ~5–10 minutes to apply | Wait, run `doctor` again |
| `HTTP 401` with Claude or another non-OpenAI model on Foundry | *Cognitive Services OpenAI User* covers only OpenAI models | Assign *Cognitive Services User* (`MODEL_ROLE="Cognitive Services User" scripts/setup-workload-identity.sh ...`) |
| `HTTP 401` later | `az login` expired, wrong tenant, or the identity lacks the role | `az login`; check the role on the Foundry resource ([foundry-setup.md](foundry-setup.md#6-step-4--give-access)) |
| `HTTP 403` | Network rules of the Foundry resource (public access disabled, IP not allowed) | Private endpoint / firewall ([foundry-setup.md](foundry-setup.md#7-step-5--networking)) |
| `HTTP 404` | Foundry: `LLM_MODEL` is the model name instead of the deployment name, wrong resource, or a deployment younger than ~5 min. Other providers: `LLM_MODEL` is not a model the endpoint serves, or `LLM_ENDPOINT` is wrong | `az cognitiveservices account deployment list -g <rg> -n <resource> -o table` |
| `HTTP 429 ... exceeded token rate limit` | The deployment's tokens-per-minute capacity is too low. A full health check uses 150k–225k tokens, ~90k in its busiest minute | Raise the capacity to 150–200 (Foundry portal → the deployment → Edit). It costs nothing extra: you pay per token |
| `HTTP 400` mentioning `reasoning_effort` | A model without reasoning rejects the parameter | `LLM_REASONING_EFFORT=` (empty) |
| `cannot reach LLM_ENDPOINT` | DNS, VPN, proxy or private endpoint | `nslookup <resource>.openai.azure.com`; the curl test in [foundry-setup.md](foundry-setup.md#8-step-6--smoke-test) |
| A check "hangs" for minutes | A request timed out (`LLM_TIMEOUT_SECONDS`, 120 s) and the SDK retries it up to 3 times | The log shows `Retrying request in ...`. A scheduled job is cancelled after `JOB_TIMEOUT_MINUTES` |
| Shallow answers, few tool calls, obvious problems missed | Reasoning switched off (gpt-5.x models reason only when asked) | Keep `LLM_REASONING_EFFORT=medium` (default for `foundry`, `openai`, `anthropic`); `high` for harder clusters |

## Reading the cluster

| Symptom | Cause | Fix |
|---|---|---|
| `doctor`: `[WARN] mcp not reachable` | kubernetes-mcp-server is not running | Locally: `npx -y kubernetes-mcp-server@0.0.67 --config deploy/local/mcp-config.toml`. The agent works without it (kubectl fallback) |
| `MCPError: ... exceeded the 1048576 byte limit` | One MCP answer over 1 MB (e.g. all events of a large cluster) | Already handled: the model is told to narrow the query; `events_list` is replaced by `warning_events` |
| `ERROR: refused: flag '--xyz' is not allowed` | The kubectl guard's allow-list | Expected for anything that is not a plain read. Add a flag only if it is harmless ([security.md](security.md)) |
| `Could not check <type>: ... forbidden` in `cluster_problems` | The identity cannot list that type | Locally: your kubeconfig's rights. In the cluster: the chart's RBAC ([In the cluster](#in-the-cluster)) |
| `doctor`: `[WARN] rbac secrets ... can read Secrets` | Your own admin kubeconfig | Normal on a laptop; the agent still never reads Secrets. In the cluster it must be `[OK]` |
| Thousands of `PolicyViolation` events | Kyverno background scans | Hidden by default (`EVENTS_EXCLUDE_REASONS`) |

## Prometheus and Alertmanager

| Symptom | Cause | Fix |
|---|---|---|
| `doctor`: `[FAIL] prometheus` / `alertmanager` | URL set but not reachable | Locally the port-forwards stopped (`error: lost connection to pod` is common): restart them, or empty the URLs in `.env` |
| Morning report: "Alerts that stopped overnight: not available" | `PROMETHEUS_URL` not set or not reachable | Set it; the alert history comes from Prometheus |

## Health checks and memory

| Symptom | Cause | Fix |
|---|---|---|
| A finding is reported as new, resolved, new again | The model judged the same evidence differently between runs (seen with `azure-cns` restarts), or used a different fingerprint | Check `k8s-servant history`. Make the rule explicit in the environment description or the skill; consider a stronger model |
| A healthy object is reported, with a name that is *almost* right | The model confused two similar names (`orders-api` vs `order-api`) | The prompt asks for exact names; report such cases, they show where the model is weak |
| Scheduled checks never say "Unchanged" | Something in the summary changes every hour (a flapping alert, a pod restarting) | Compare two `cluster_problems` outputs; add always-firing alerts to `ALERTS_IGNORE_NAMES` |
| `check` exits 1, "no valid findings JSON" | The model forgot or broke the JSON twice | Memory is unchanged by design; the answer is in `k8s-servant history <id>` |
| Configuration error about `DATA_DIR` (exit 2) | `DATA_DIR` cannot be created or written | Fix the path or permissions |
| Memory looks wrong | — | Delete `DATA_DIR/memory.db` (and `notes.md`) to start fresh ([memory.md](memory.md#backup-loss-and-reset)) |

## Teams

| Symptom | Cause | Fix |
|---|---|---|
| A check is not posted | `TEAMS_POST_MODE=changes` and nothing is new or resolved | Expected. `TEAMS_POST_MODE=always` to see every check |
| `Teams webhook is not configured` | Neither `TEAMS_WEBHOOK_URL_FILE` nor `TEAMS_WEBHOOK_URL` is set | [README](../README.md#ms-teams-setup) |
| `Posting to Teams failed: HTTP 4xx` | Wrong or revoked URL, or the flow is turned off | Check the Workflow in Teams; copy the URL again |
| No morning report | Weekend, already sent today, `MORNING_REPORT_ENABLED=false`, or `serve` not running | `k8s-servant history` shows `morning` runs |

## In the cluster

| Symptom | Cause | Fix |
|---|---|---|
| Helm render fails / HelmRelease not Ready: `azureWorkloadIdentity.clientId is required` | Workload Identity is enabled but the client ID is not in your values | Run `scripts/setup-workload-identity.sh` and put the printed client ID into your values, or set `azureWorkloadIdentity.enabled: false` ([deployment.md](deployment.md#3-values-for-the-cluster)) |
| Helm render fails: `extraEnv … looks like a credential` | A credential-like name (`API_KEY`, `WEBHOOK_URL`, `TOKEN`, `PASSWORD`, `SECRET`) has a plain `value` | Put the value in a Secret and use `valueFrom.secretKeyRef` ([deployment.md](deployment.md#extension-points)) |
| Pod stuck in `ContainerCreating`: `secret "…" not found` | The Secret named in `llmApiKey.existingSecret` or `teamsWebhook.existingSecret` does not exist (yet) in the release namespace | Create it, or check that your secret tool (for example the ExternalSecret) has synced it: `kubectl -n k8s-servant get secret,externalsecret` |
| Pod stuck in `ContainerCreating`: `couldn't find key api-key in Secret …` | The Secret exists but holds the value under another key | Rename the key in the Secret, or set `llmApiKey.secretKey` / `teamsWebhook.secretKey` to the key it uses |
| Pod not created: an admission policy denies it (e.g. Kyverno image signature or registry rules) | The policy does not accept `ghcr.io` images or does not know the cosign identity | Allow the published images and their keyless signature ([deployment.md](deployment.md#published-artifacts)), or mirror both images into an allowed registry and set `image.repository` / `mcp.image.repository` |
| `ImagePullBackOff` for `k8s-servant:<version>` | `image.tag` points to a version that was never released, no egress to `ghcr.io`, or a mirror that lacks the tag | Leave `image.tag` empty (the chart's `appVersion` is used); check egress or the mirror |
| Model calls and the ExternalSecret fail: `AADSTS70021`, `No matching federated identity record found` | The federated credential points to an old OIDC issuer (the cluster was recreated) | Re-run `setup-workload-identity.sh` ([deployment.md](deployment.md#when-a-cluster-is-recreated)) |
| `doctor`: model `timed out` / `connection refused` | Egress to the Foundry endpoint is blocked | Firewall rule for `<foundry>.openai.azure.com:443` ([deployment.md](deployment.md#2-network)) |
| ExternalSecret `SecretSyncedError`, `403` | The identity has no read access to the Key Vault secret yet (or role propagation) | Re-run the script; wait a few minutes |
| Pod restarts every few minutes, `Liveness probe failed` | The scheduler stopped touching `/tmp/heartbeat` (stuck run) | `kubectl -n k8s-servant logs deploy/k8s-servant -c agent --previous` |
| `doctor`: MCP `unreachable` | The sidecar is not running or crashed | `kubectl -n k8s-servant logs deploy/k8s-servant -c mcp`; the agent uses kubectl meanwhile |
| `Forbidden` for some resource type | Not covered by `view` or the cluster reader role | Add a read-only rule through `rbac.extraRules` if the agent really needs it |

## Local setup

| Symptom | Cause | Fix |
|---|---|---|
| `.venv/bin/...: bad interpreter` | Homebrew upgraded Python and the venv's link broke | `rm -rf .venv && python3.14 -m venv .venv && .venv/bin/pip install -e '.[dev]'` |
| Settings seem ignored | `.env` was not loaded into this shell | `set -a; source .env; set +a` in every new terminal |

## Cost higher than expected

- Check the log lines `model call: N prompt (M cached) + K completion tokens`. Cached
  prompt tokens are billed at a discount.
- Most hourly checks should be `Health check skipped ... unchanged`. If not, see
  "Scheduled checks never say Unchanged" above.
- `TOOL_OUTPUT_MAX_CHARS` (default 8,000) limits how much each tool result adds to
  every later call.
- Fewer full checks: a longer `CHECK_INTERVAL_MINUTES` or `CHECK_FULL_INTERVAL_HOURS`.
- Set a budget alert on the Foundry resource group ([foundry-setup.md](foundry-setup.md#9-cost-and-limits)).
