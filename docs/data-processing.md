# Data processing: what happens to cluster data before and after the model

The agent does not pass raw cluster data to the model. Most data is read, filtered,
grouped, summarised or cut **in Python first**, and the model's answers are checked and
normalised **before** anything is stored or posted. This page lists every such step,
and also what is **not** done, so that a security or privacy review can rely on it.

The flow:

```
 cluster / Prometheus / Alertmanager
        │  read (read-only; Secrets never requested)
        ▼
 ① reduce in Python      summaries, filters, grouping, formatting      (tools)
        ▼
 ② cap the size          every tool result ≤ TOOL_OUTPUT_MAX_CHARS      (agent loop)
        ▼
 ③ the request           system prompt + conversation + tool results ──▶ the model (any provider)
        ▼                                                                    │
 ④ check the answer      findings JSON parsed, validated, normalised  ◀─────┘
        ▼
 ⑤ compute facts         new / still open / resolved, the night's story  (memory, report)
        ▼
 ⑥ shape the output      Markdown report, Teams card shrunk to fit       (teams)
```

Plus one path where **no data reaches the model at all**: the "anything changed?"
check of scheduled runs.

## ① Reduction in Python, per tool

| Tool | Raw data read | What Python does before the model sees it | Typical result |
|---|---|---|---|
| `cluster_problems` | Full JSON of all nodes, pods, Deployments, StatefulSets, DaemonSets, PDBs, ResourceQuotas, PVCs, Jobs (a large test cluster: ~1,250 pods, 615 Deployments, ~400 PDBs, ~570 quotas) | Evaluates health rules per object; keeps **only unhealthy** ones; groups pods by owning workload (one line per Deployment, not per pod); parses quantities (`4Gi`, `500m`) to compute quota usage; keeps failed Jobs of the last 24 h only; at most 50 lines per section; adds counts of what was checked | **787 characters** |
| `warning_events` | Warning events | The **API server** drops the reasons in `EVENTS_EXCLUDE_REASONS` (default `PolicyViolation`) before they are sent; Python keeps only events within the time window, groups them by (namespace, object, reason), **sums the counts**, keeps the latest time and message, collapses whitespace, **cuts each message to 200 characters**, sorts by frequency, keeps at most 60 groups | test cluster: 3,710 events → 207 in 18 groups, **3.3 KB** |
| `prometheus_query` | Prometheus JSON | One line per series: `name{labels} value`, numbers in short form | a few lines |
| `prometheus_query_range` | All samples of a range | **Each series reduced to 6 numbers**: points, first, last, min, max, and the time range. The raw samples never reach the model | one line per series |
| `alertmanager_alerts` | Alert JSON | Only active, not silenced, not inhibited alerts; one line each: name, severity, namespace/pod/node/job, start time, the `summary` (or `description`) annotation; most severe first. Other labels and annotations are dropped | one line per alert |
| MCP tools | Text from kubernetes-mcp-server | Text blocks passed through; non-text content replaced by `[… content omitted]`; errors prefixed with `ERROR:` and advice | as returned |
| `kubectl` | kubectl's output | Passed through; stderr appended under `[stderr]`; failures and timeouts become `ERROR:` text | as returned |
| `load_skill` | A skill's Markdown | Passed through | 2–5 KB |

For the morning report's history, two more reductions happen without the model:

- **Alerts that fired overnight** come from one Prometheus query that already
    aggregates (`max by (alertname, severity, namespace, pod, node, job)`); Python then
    drops always-firing alerts (`ALERTS_IGNORE_NAMES`), info-level alerts and those still
    firing.
- **Findings history** is computed from memory (below); only the result is sent.

## ② Size cap on every tool result

Whatever a tool returns, the agent loop cuts it to `TOOL_OUTPUT_MAX_CHARS` (default
8,000) before it is added to the conversation: two thirds from the head, one third
from the tail, and a marker in between telling the model to narrow the query. The head
of a listing has the columns and first rows; the tail of a log has the latest lines.
This protects the context window and the cost: every later model call resends the
whole conversation.

## ③ What the request to the model contains

| Part | Content | Processing |
|---|---|---|
| System prompt | Rules, environment description, cluster notes, learned notes, skill catalog | Assembled per run; learned notes cut to the newest `NOTES_MAX_CHARS` (8,000) at a line boundary ([how-the-agent-learns.md](how-the-agent-learns.md)) |
| The task | A question, or the health-check request with the **previously open findings** (fingerprint, severity, title, since when) | Built by `healthcheck.build_check_prompt()` |
| Tool definitions | Names, descriptions, argument schemas | — |
| Conversation | The model's earlier messages and the tool results (after ① and ②) | — |

The **morning report's overview** is a separate request **without tools**: the model
gets only the facts computed in ⑤ (as Markdown) and writes 3–6 sentences. It cannot
look anything up, so it can only summarise what Python gave it.

## ④ Checking the model's answer

The model's output is never trusted as-is:

- **Findings JSON.** The answer of a health check must end with a JSON block. It is
    parsed and validated: it must be an object with a `findings` list, and every finding
    must have a fingerprint. An unknown severity becomes `warning` (logged); a missing
    title falls back to the fingerprint. Invalid → one follow-up asking only for the
    JSON; still invalid → the run is recorded as failed and **memory is not changed**.
- **Fingerprints are normalised** (lower case, spaces around `/` removed, words joined
    with `-`), because the model does not always spell them the same way.
- **Duplicate fingerprints** in one answer are merged, keeping the most severe one.
- **Learned notes** (`save_note`): whitespace and newlines collapsed to one line; rejected
    if empty or longer than 500 characters; logged at WARNING.
- **Tool calls**: unknown tools, invalid JSON arguments and invalid values
    (namespace, event reason, time window, kubectl flags) are refused with `ERROR:` text
    before anything runs.

## ⑤ Facts computed in Python, not by the model

| Fact | How |
|---|---|
| New / still open / resolved | Memory compares the current fingerprints with the open ones (`Memory.apply_check()`) |
| The morning report's window | Since the previous morning report, max 72 h (`report_window_start()`) |
| New overnight, resolved overnight, came and went | Queries on `first_seen` / `resolved_at` in the window |
| Checks run / skipped / failed | Counted from the run history |
| "Anything changed?" for scheduled checks | A SHA-256 hash of the problem lines of `cluster_problems` and the alerts' identity (name, severity, location; not their start time or text). **Compared locally; nothing is sent to the model** |

The model never decides what is "new" or "resolved"; it only reports what it sees now.

## ⑥ Shaping the output

- **Markdown reports** (stdout, history): rendered from the parsed findings and the
    facts, not copied from the model's free text (except the summary/overview).
- **Teams cards**: built from the same data; if larger than 27,000 bytes, details are
    shortened step by step and lists cut with "… and N more".
- **Logs**: tool calls are logged with their arguments and output **size**; full
    outputs only at DEBUG. The webhook URL is never logged.

## What is NOT done

Being explicit matters here:

- **No redaction or masking.** Text that is passed through (logs from `pods_log` or
    `kubectl logs`, event messages, ConfigMap contents, annotations, describe output)
    reaches the model as it is. If an application writes personal data or a credential
    into its logs, the model can see it, and it can end up in an answer, the history and
    Teams. Secrets themselves are never read (RBAC, MCP denied resources, the kubectl
    guard).
- **No anonymisation.** Object names, namespaces, node names and IP addresses in
    messages are sent unchanged; the model needs them to investigate.
- **No local filtering of what the model asks for.** Within the read-only tools, the
    model chooses what to read (for example which pod's logs). Limits apply (tail, size
    cap), content filtering does not.

Mitigations: logs are read only when investigating a specific problem, with a tail;
you choose the provider, so read its data and retention terms
([list of links](model-providers.md#data-handling-and-errors)) or run the model inside
your network (Ollama or another OpenAI-compatible server); the Responses API (Foundry,
OpenAI) is called with `store=false`, so no conversation is stored by the provider;
with Azure AI Foundry the model runs in a dedicated resource in the EU data zone and
does not train on prompts; Secrets are out of reach; keep credentials out of logs and ConfigMaps
(good practice anyway). If redaction is needed later, the natural place is the agent
loop's step ②, where every tool result passes. See [security.md](security.md).

## Where this lives in the code

| Step | Code |
|---|---|
| ① | `tools/cluster_tools.py`, `tools/prometheus_tool.py`, `tools/mcp_tools.py` (`_result_to_text`), `tools/kubectl_tool.py` (`run_kubectl`) |
| ② | `tools/registry.py` (`truncate_output`), called in `agent.py` |
| ③ | `agent.py` (`build_system_prompt`), `healthcheck.py` (`build_check_prompt`), `report.py` (`OVERVIEW_PROMPT`) |
| ④ | `findings.py` (`parse_findings`, `normalize_fingerprint`), `healthcheck.py` (`run_check`), `memory.py` (`add_note`) |
| ⑤ | `memory.py` (`apply_check`, `findings_changed_since`), `report.py`, `healthcheck.py` (`sweep_signature`) |
| ⑥ | `healthcheck.py` (`render_check_report`), `report.py`, `teams.py` |
