# The `k8s-servant` command

`k8s-servant` is how you talk to the agent, and in the cluster it is also the agent's
main process (`k8s-servant serve`). One command works in two places:

| Where | How the agent reaches the cluster | How it reaches the model | Memory | Available |
|---|---|---|---|---|
| **Your laptop** | your current kubeconfig context (and a local kubernetes-mcp-server, if you start one) | an API key file (`LLM_API_KEY_FILE`), nothing for Ollama, or `az login` for Microsoft Foundry with Entra ID | `./.data` | now |
| **Inside the agent's pod** | the pod's read-only ServiceAccount and the MCP sidecar | an API key from a mounted Secret, nothing for an in-cluster Ollama, or Workload Identity for Microsoft Foundry | `/data` (shared with the scheduler) | now ([deployment.md](deployment.md)) |

Settings come from environment variables; see the [settings reference](configuration.md).
On a laptop, put them in `.env` and load it into your shell with `set -a; source .env; set +a`.

## Synopsis

```
k8s-servant [--values FILE ...] [--knowledge-dir DIR] [--log-level LEVEL] COMMAND

  ask "question"     ask the agent a question about the cluster
  check [--post]     run one health check; --post sends it to Teams (per TEAMS_POST_MODE)
  report [--post]    produce the morning report now; --post sends it to Teams
  serve              hourly checks + the morning report until stopped (the pod's main process)
  findings [--all]   open findings from memory (--all: resolved ones too)
  history [ID]       past runs, or the full report of one run
  notes              the agent's learned notes
  tools              the tools available right now (MCP or kubectl fallback)
  doctor             check configuration and connectivity, step by step
  onboard            scan the cluster once and draft its knowledge files for review
```

`python -m k8s_servant ...` is the same as `k8s-servant ...`.

## Global options

| Option | Meaning |
|---|---|
| `--values FILE` | Read the `knowledge:` section of a Helm values file. Repeatable; later files win. Use the same files the cluster uses, so your laptop sees the same environment description and skills as the cluster: `--values knowledge-values.yaml --values my-values.yaml` |
| `--knowledge-dir DIR` | Knowledge folder. Overrides `KNOWLEDGE_DIR`. Default: the chart's `charts/k8s-servant/knowledge` folder, which holds the built-in skills |
| `--log-level LEVEL` | `DEBUG`, `INFO` or `WARNING`. Overrides `LOG_LEVEL`. Logs go to stderr |

Tip: define an alias for local work on one cluster:

```bash
alias kss='k8s-servant --values examples/knowledge-values.yaml --values my-values.yaml'
```

## `ask`

Asks one question and prints the answer (Markdown) to **stdout**. Progress logs and a
one-line summary go to **stderr**. The question and the answer are stored in the
history (`k8s-servant history`).

```console
$ k8s-servant ask "Are there any unhealthy pods or nodes?"
...INFO k8s_servant.agent: tool load_skill({"name": "cluster-health-check"}) -> 3012 chars in 0.0s
...INFO k8s_servant.agent: tool pods_list({}) -> 20000 chars in 1.2s
...
**Overall: 1 warning, no critical issues.**

1. **Deployment `payments-dev/api` — CrashLoopBackOff** (warning) ...

-- 6 model calls, 9 tool calls, tools: knowledge, kubectl, mcp, memory
```

- Quotes are optional: `k8s-servant ask why is pod x pending` works.
- Save just the answer: `k8s-servant ask "..." > answer.md`.
- If the agent reaches `MAX_ITERATIONS`, it still answers with what it found, and the
    summary line says `stopped at MAX_ITERATIONS`.
- The agent never changes anything. Ask it to fix something and it describes the fix.

## `check`

Runs one health check: the agent follows the `cluster-health-check` skill, reports its
findings as JSON, and memory compares them with the previous check. The Markdown
report is printed and stored. See [memory.md](memory.md) for how
new / still open / resolved works.

```console
$ k8s-servant check
# Health check — my-cluster — 2026-10-12 06:00 UTC

One workload is crash-looping; everything else is healthy.

**1 new · 2 open · 0 resolved**

## New

- **[CRITICAL] Deployment api crash-loops** — `crashloop/payments-dev/deployment/api`
  Container api exits with code 1 since 05:40 UTC: "cannot connect to db:5432". ...

## Still open

- [warning] ResourceQuota full in shop-dev — open since 2026-10-10 09:00 UTC — `quota/shop-dev/resourcequota/default-ns-compute`
```

`--post` sends the result to Teams according to `TEAMS_POST_MODE`:

| `TEAMS_POST_MODE` | Posted when |
|---|---|
| `changes` (default) | something is new or resolved |
| `always` | every check, including failed ones |
| `off` | never |

Running `check --post` twice in a row posts once. The second run has nothing new or
resolved, unless the cluster changed in between.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | The check succeeded |
| 1 | No valid findings came back (memory unchanged), or the model call failed |
| 2 | Configuration error |

## `report`

Produces the **morning report** now and prints it: how the cluster did since the
previous morning report (usually the night; on Mondays the weekend; at most 72 h, 24 h
if there was none) and what needs attention today:

- it runs a fresh health check;
- from memory: open findings; what appeared overnight and is still open, what was
    resolved, and what **came and went** during the night; how many checks ran in full,
    were skipped as unchanged, or failed;
- alerts **firing now** (Alertmanager) and alerts that **fired overnight and stopped**
    (Prometheus' `ALERTS` history; warning and critical only, without
    `ALERTS_IGNORE_NAMES`);
- finally a short overview written by the model: how the night went and what to keep
    an eye on today.

`--post` sends it to Teams unless `TEAMS_POST_MODE=off`.

```console
$ k8s-servant report
# Morning report — my-cluster — Mon 12 Oct 2026

_Covers the time since Fri 09 Oct 08:00 (72 h)._

A mostly quiet weekend. One new crash loop in payments-dev needs attention today; the
shop-dev quota has been full for two days. A rollout in billing-dev was briefly blocked
by its quota on Saturday night and recovered by itself.

## At a glance

- Covers: since Fri 09 Oct 08:00 (72 h)
- Open findings: 2 (critical 1, warning 1, info 0)
- Overnight: 1 new, 1 resolved, 1 came and went
- Health checks: 72: 9 full, 61 unchanged (model skipped), 2 failed
- Latest check: ok
- Alerts firing now: 3
- Alerts that stopped overnight: 1
...
```

The scheduled version (`serve`) runs at `MORNING_REPORT_TIME` (default 08:00
UTC, see `MORNING_REPORT_TIMEZONE`) on weekdays. Running `report` by hand does not affect it.

## `serve`

The scheduler, and the pod's main process in the cluster:

- it runs a health check right away and then every `CHECK_INTERVAL_MINUTES` (default 60);
- it runs the morning report when it is due;
- it posts both to Teams according to `TEAMS_POST_MODE`;
- it touches `HEARTBEAT_FILE` (if set) at least once a minute for the liveness probe.

A failing run is logged and the loop continues. A run longer than
`JOB_TIMEOUT_MINUTES` (default 30) is cancelled. SIGTERM or Ctrl-C cancels a running
check or report and stops at once, so a pod shuts down within its grace period. The
heartbeat keeps being touched while a run is in progress.

```console
$ CHECK_INTERVAL_MINUTES=5 k8s-servant serve
...INFO k8s_servant.scheduler: Scheduler started: health check every 5 min; morning report at 08:00 UTC on weekdays
...INFO k8s_servant.healthcheck: Health check done in 74s: 0 new, 2 open, 0 resolved
...INFO k8s_servant.cli: Not posting the health check to Teams (TEAMS_POST_MODE=changes)
```

## `findings`

Shows the open findings from memory, most severe first. `--all` includes resolved
ones.

```console
$ k8s-servant findings
critical open                         3 h 5 min  crashloop/payments-dev/deployment/api  Deployment api crash-loops
warning  open                       2 d 1 h      quota/shop-dev/resourcequota/default-ns-compute  ResourceQuota full in shop-dev
```

## `history`

Lists recent runs (checks, morning reports, questions), or prints the full report of
one run.

```console
$ k8s-servant history
   42  2026-10-12 06:00  check  ok     new 1   open 2   resolved 0   One workload is crash-looping; ...
   41  2026-10-12 05:00  morning ok     new 0   open 1   resolved 1   Mostly healthy; ...
   40  2026-10-11 14:12  ask    ok     new 0   open 0   resolved 0   why is web pending in shop-dev?
$ k8s-servant history 42
# Health check — my-cluster — 2026-10-12 06:00 UTC
...
```

`--limit N` changes how many runs are listed (default 20). Unknown ids exit with code 1.

## `notes`

Prints the agent's learned notes (`notes.md`). These are facts the agent saved with
its `save_note` tool so it does not have to rediscover them. See
[memory.md](memory.md#learned-notes).

## `tools`

Shows which tools the agent has right now, grouped by where they come from, followed
by the skills and the files they were loaded from. Use it to check that MCP is
connected, Prometheus is configured and your skills were picked up.

```console
$ k8s-servant tools
mcp        pods_list               List all the Kubernetes pods in the current cluster ...
...
kubectl    kubectl                 FALLBACK: use only when none of the Kubernetes MCP tools can answer. ...
cluster    cluster_problems        START HERE for any health question. Reads nodes, pods, Deployments, ...
cluster    warning_events          Warning events grouped by object and reason, most frequent first, ...
prometheus prometheus_query        Run an instant PromQL query against the cluster's Prometheus ...
prometheus alertmanager_alerts     List the alerts firing right now in Alertmanager ...
knowledge  load_skill              Load the full step-by-step procedure of a skill ...
memory     save_note               Remember one short fact for future runs ...

Skills:
  cluster-health-check      .../charts/k8s-servant/knowledge/skills/cluster-health-check/SKILL.md
  ...
```

When the MCP server is not running, the first line reads
`MCP server not reachable or disabled: the kubectl fallback is the only cluster tool.`

## `doctor`

Checks everything the agent depends on, one line per check, and makes one tiny real
model call. Run it first whenever something does not work.

```console
$ k8s-servant --values examples/knowledge-values.yaml --values my-values.yaml doctor
[OK  ] config           anthropic / claude-sonnet-5-5 via Messages API, API key (file /home/me/.config/k8s-servant/llm-api-key)
[OK  ] knowledge        environment: yes, cluster notes: yes, skills: 5
[OK  ] memory           .data: 2 open findings, notes 312 chars
[OK  ] mcp              13 tools from http://localhost:8080/mcp
[OK  ] kubectl          Client Version: v1.37.1 | Kustomize Version: v5.8.1 | Server Version: v1.33.8
[WARN] rbac secrets     this identity can read Secrets; fine on a laptop, must be 'no' in the cluster
[OK  ] prometheus       http://localhost:9090
[OK  ] alertmanager     http://localhost:9093
[OK  ] teams            webhook configured, mode changes (doctor does not post)
[OK  ] model            answered 'OK' in 1.4s
```

| Check | What it proves | When it is not OK |
|---|---|---|
| `config` | `LLM_PROVIDER`, `LLM_MODEL` (and `LLM_ENDPOINT` where required) are set and the sign-in settings are valid. The line names the provider, API and sign-in, for example `anthropic / claude-sonnet-5-5 via Messages API, API key (file /var/run/secrets/llm/api-key)`, plus a warning when Workload Identity is available but a key is configured | Set them; see [model-providers.md](model-providers.md) |
| `knowledge` | Environment description, cluster notes and skills were loaded | WARN only: without them the agent knows nothing site-specific. Pass `--values` |
| `memory` | `DATA_DIR` is usable | An unwritable folder is a configuration error (exit 2) before doctor runs |
| `mcp` | The MCP server answered | WARN only: the kubectl fallback is used |
| `kubectl` | `kubectl version` reached the API server | Check your kubeconfig context or VPN, or the pod's ServiceAccount |
| `rbac secrets` | Whether this identity can read Secrets | WARN is normal on a laptop. In the cluster it must be OK (`no`) |
| `prometheus`, `alertmanager` | `/-/ready` answered | WARN when not configured (optional); FAIL when configured but unreachable |
| `teams` | A webhook URL is configured (nothing is posted) | WARN: reports go only to logs and history |
| `model` | Endpoint, authentication, deployment name and network all work | The detail says what to fix (401, 403, 404, 429, network) |

The exit code is `1` if any check reports FAIL, otherwise `0`. What to do about a
WARN or FAIL: [troubleshooting.md](troubleshooting.md).

## `onboard`

Scans the cluster once and writes a draft of its knowledge files for you to review:
what runs there, the network and monitoring setup, and what is normal noise. Every
statement in the draft carries its evidence, and anything the scan is unsure of
becomes a `TODO(onboard):` line. In a terminal it first asks whether this cluster is
representative of your platform, then asks about each doubt and about what it cannot
see (firewalls, maintenance windows). The scan is read-only: it runs only fixed
`kubectl get` and `kubectl version` calls (plus GET requests to Prometheus and
Alertmanager, directly or through the API server's service proxy) and never reads
Secrets. It never overwrites
existing files unless you pass `--force`. It prints the paths it wrote on stdout.
With a model configured, it adds a short overview written from the proven facts only.

| Flag | Meaning |
|---|---|
| `--out DIR` | Folder for the draft. Default: `k8s-servant-onboard` |
| `--format {values,markdown}` | `values` (default): Helm values files (`knowledge-values.yaml` for the platform when representative, `<cluster>.yaml` for this cluster, plus `settings.env`). `markdown`: `environment.md`, `cluster.md` and `settings.env` |
| `--force` | Overwrite files that already exist |
| `--no-input` | Ask nothing; every doubt stays a `TODO(onboard):` line |
| `--no-model` | Do not ask the model for the overview |
| `--cluster-name NAME` | Cluster name for the draft and the file name. Default: `CLUSTER_NAME`, else `my-cluster` |

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Something failed at runtime: a model call, a check without valid findings, a FAIL in `doctor`, an unknown run id |
| 2 | Configuration error: missing setting, bad value, unreadable or malformed `--values` file, invalid endpoint, unwritable `DATA_DIR` |
| 130 | Interrupted with Ctrl-C |

## Running inside the pod

```bash
kubectl -n k8s-servant exec -it deploy/k8s-servant -c agent -- k8s-servant ask "What changed in the last hour?"
kubectl -n k8s-servant exec -it deploy/k8s-servant -c agent -- k8s-servant findings
kubectl -n k8s-servant exec -it deploy/k8s-servant -c agent -- k8s-servant doctor
```

Inside the pod every setting is already provided by the chart, and the commands share
memory with the running scheduler.
