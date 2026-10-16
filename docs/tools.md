# Tools reference

The model never touches the cluster itself. It can only ask the agent to run one of
the **tools** below, and every tool only reads. This page lists each tool, what it
reads, its safety rules, and when the model is expected to use it.

`k8s-servant tools` shows which tools are available in your session right now.

## Overview

| Tool | Group (`surface`) | Reads | Available when |
|---|---|---|---|
| `cluster_problems` | `cluster` | Nodes, pods, Deployments, StatefulSets, DaemonSets, PodDisruptionBudgets, ResourceQuotas, PVCs, Jobs | always (needs `kubectl`) |
| `warning_events` | `cluster` | Warning events | always (needs `kubectl`) |
| MCP tools (`pods_list`, `pods_get`, `pods_log`, `resources_list`, `resources_get`, `nodes_top`, `pods_top`, `nodes_log`, `nodes_stats_summary`, `namespaces_list`, ...) | `mcp` | Single objects, lists, logs, metrics from the Kubernetes API | kubernetes-mcp-server reachable at `MCP_URL` |
| `kubectl` | `kubectl` | Anything a read verb can show, except Secrets | always |
| `prometheus_query`, `prometheus_query_range` | `prometheus` | PromQL | `PROMETHEUS_URL` set |
| `alertmanager_alerts` | `prometheus` | Alerts firing now | `ALERTMANAGER_URL` set |
| `load_skill` | `knowledge` | A skill's full procedure | there are skills |
| `save_note` | `memory` | (writes a learned note to the agent's own memory) | in every session with memory |

Tools are registered in this order: MCP tools first (so they win a name clash), then
`kubectl`, `cluster_problems`, `warning_events`, Prometheus, `load_skill`, `save_note`
(`runtime.open_runtime()`).

## How the model is told to use them

- **Overall health questions start with the summary tools** (`cluster_problems`,
  `warning_events`). They return only what is wrong, so they work on clusters that are
  far too large to list (a test cluster: 615 Deployments, 1,252 pods, 3,700 Warning events).
- **Single objects, logs and metrics: MCP tools.** They are the preferred way to read
  details.
- **`kubectl` is the fallback.** Its description starts with `FALLBACK:` when MCP is
  available; when MCP is not, it is the only detailed tool and the system prompt says so.
- **Skills first.** The model looks at the skill catalog, loads the matching skill and
  follows it (`load_skill`).

## Every tool result is text, and errors never stop the investigation

- A tool result longer than `TOOL_OUTPUT_MAX_CHARS` (default 8,000) is cut: two thirds
  from the head, one third from the tail, with a marker telling the model to narrow
  the query. Every later model call resends earlier results, so this limit also drives
  the token cost.
- Failures come back as text starting with `ERROR:` instead of stopping the run: an
  unknown tool, invalid arguments, a crash, a refused kubectl command, a timeout, or an
  MCP call that failed. A failed MCP call also tells the model to narrow the query or
  use another tool.

## `cluster_problems`

**Purpose.** The first step of every health check: what is unhealthy in the whole
cluster, in a few hundred characters.

**What it does.** Runs these commands in parallel and checks the JSON in Python
(`tools/cluster_tools.py`):

```
kubectl get nodes -o json                    (skipped when a namespace is given)
kubectl get pods|deployments|statefulsets|daemonsets|poddisruptionbudgets|
            resourcequotas|persistentvolumeclaims|jobs  -A -o json   (or -n <namespace>)
```

**What counts as a problem.**

| Object | Reported when |
|---|---|
| Node | Ready is not True; MemoryPressure / DiskPressure / PIDPressure / NetworkUnavailable is True; cordoned |
| Pod (grouped by owning workload) | Failed (with reason, e.g. Evicted); a container waiting (CrashLoopBackOff, ImagePullBackOff, CreateContainerConfigError, ...); Pending (with the scheduler's reason); ≥ 5 restarts with the last one in the last 24 h; Running but not ready. Terminating pods are ignored |
| Deployment | fewer available than desired, a `ReplicaFailure` condition (e.g. quota) or `Progressing=False` |
| StatefulSet / DaemonSet | fewer ready than desired |
| PodDisruptionBudget | matches no pods (`expectedPods 0`), or fewer healthy pods than needed. A healthy single-replica budget is not reported |
| ResourceQuota | a resource used at ≥ 90% of its limit |
| PVC | not Bound |
| Job | failed in the last 24 h (with its CronJob) |

Pods are grouped by owner, and a ReplicaSet `<name>-<hash>` stands for its Deployment,
so the lines match the finding fingerprints. Each section shows at most 50 lines.

**Example (test cluster, 2026-10-12).**

```
Cluster problem sweep, all namespaces, 2026-10-12 11:41 UTC
Checked: 15 nodes, 1252 pods, 615 deployments, 3 statefulsets, 23 daemonsets, 397 PDBs, 572 quotas, 6 PVCs, 4 jobs
Problems found: 3

## Pods (grouped by owning workload)
- DaemonSet kube-system/azure-cns: 1 of its pods unhealthy: 18 restarts, last Error at 03:05 UTC; e.g. pod azure-cns-x7k2p

## PodDisruptionBudgets
- PodDisruptionBudget shop/orders-api: matches no pods (expectedPods 0; ...)
- PodDisruptionBudget search/search-v2: matches no pods (expectedPods 0; ...)
```

If a resource type cannot be read (RBAC, timeout), the output says
`Could not check <type>: ...` and the other types are still checked.

**Arguments.** `namespace` (optional). It must be a valid namespace name
(lower-case letters, digits and `-`, at most 63 characters); anything else is refused.

**Not covered** (the model checks these itself): node CPU/memory usage, Flux objects,
events, certificates, anything outside the listed types.

## `warning_events`

**Purpose.** What has been going wrong recently, without the noise.

**What it does.**

```
kubectl get events -A --field-selector type=Warning,reason!=PolicyViolation -o json
```

Events are grouped by (namespace, object, reason) with their total count, the last
time and the latest message, most frequent first, at most 60 groups.

**Noise filter.** Reasons in `EVENTS_EXCLUDE_REASONS` (default `PolicyViolation`) are
filtered out **by the API server**: on a test cluster, 3,550 of 3,710 Warning events were
Kyverno background-scan results. The output says which reasons were hidden. The model
can still see them with `include_excluded=true` or `reason=PolicyViolation`.

**Arguments.** `namespace`, `since_minutes` (1–10080, default 60), `reason` (one
event reason, letters and digits only), `include_excluded` (true/false). Each is
validated, so model text cannot change the field selector or the command.

MCP's own `events_list` is **not** offered to the model: it returns every event
unfiltered, and on a large test cluster that exceeded the MCP client's 1 MB message limit.

## MCP tools (kubernetes-mcp-server)

Provided by [kubernetes-mcp-server](https://github.com/containers/kubernetes-mcp-server)
v0.0.67 with the `core` toolset in read-only mode
([`deploy/local/mcp-config.toml`](../deploy/local/mcp-config.toml)). Names,
descriptions and arguments come from the server unchanged.

| Tool | Use |
|---|---|
| `pods_list`, `pods_list_in_namespace` | pods, with label and field selectors |
| `pods_get` | one pod |
| `pods_log` | container logs (`tail`, `previous` for a crashed container) |
| `pods_top`, `nodes_top` | CPU and memory usage (metrics-server) |
| `resources_list`, `resources_get` | any resource type by apiVersion and kind |
| `namespaces_list` | namespaces |
| `nodes_log`, `nodes_stats_summary` | node logs and kubelet statistics |

Safety: `read_only = true`, Secrets denied (`[[denied_resources]]`), listening on
`127.0.0.1` only. The `config` toolset (returns the raw kubeconfig) and `helm` (reads
Secrets) are off. See [security.md](security.md).

## `kubectl`

A guarded fallback for anything the other tools cannot show, for example
`describe`, `auth can-i` or a field selector MCP does not support.

- **Verbs:** `get`, `describe`, `logs`, `top`, `events`, `api-resources`,
  `api-versions`, `explain`, `version`, `cluster-info`, `auth can-i`.
- **Flags:** only an allow-list of read flags (`-n`, `-A`, `-l`, `--field-selector`,
  `--sort-by`, `-o`, `--tail`, `--since`, `-c`, `-p`, ...), parsed the way kubectl parses
  them. **Output formats:** `wide`, `yaml`, `json`, `name`, `jsonpath=...`,
  `custom-columns=...`.
- **Never:** Secrets, write verbs, `exec`, `port-forward`, flags that change the server,
  user or kubeconfig, template files.
- No shell: arguments are passed as a list.

Why an allow-list and which bypasses it closes: [security.md](security.md#why-the-kubectl-guard-is-an-allow-list).

## `prometheus_query`, `prometheus_query_range`, `alertmanager_alerts`

Registered only when `PROMETHEUS_URL` / `ALERTMANAGER_URL` are set. GET requests only.

- `prometheus_query`: an instant PromQL query (optionally at a past `time`).
- `prometheus_query_range`: a query over a time range; each series is summarised as
  first / last / min / max with timestamps ("when did it start", "is it getting
  worse").
- `alertmanager_alerts`: alerts firing now (not silenced, not inhibited), most severe
  first, optionally filtered by label matchers.

The `prometheus-queries` skill holds proven queries. An empty result is not proof
that all is well.

## `load_skill`

Returns the full procedure of a skill from the skill catalog. Only names and
descriptions are in the system prompt; the body is loaded when needed. See
[knowledge-and-skills.md](knowledge-and-skills.md).

## `save_note`

The only tool that writes, and only to the agent's own memory (`notes.md`), never to
the cluster. One sentence, at most 500 characters, no secrets. Every new note is
logged at WARNING and listed in the next morning report, because a note changes what
every future run treats as normal. See [memory.md](memory.md#learned-notes).

## Tools that run without the model

Scheduled checks run `cluster_problems` and `alertmanager_alerts` **before** calling
the model. If problems and alerts are identical to the last full check, the model is
not called at all. See [reports.md](reports.md#when-the-model-is-skipped).
