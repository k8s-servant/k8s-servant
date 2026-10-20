# Architecture

This page explains how k8s-servant is built and why it is built that way.

Related pages: every tool in [tools.md](tools.md); health checks, the morning report
and Teams cards in [reports.md](reports.md); what goes wrong and how to fix it in
[troubleshooting.md](troubleshooting.md).

> **Where it runs.** The agent runs on a laptop (CLI) and in a cluster: one pod with the
> agent and the MCP sidecar, installed from the Helm chart in `charts/k8s-servant/`
> ([deployment.md](deployment.md)).

## The big picture

```
                         ┌───────────────────────── k8s-servant (Python) ─────────────────────────┐
  question ─────────────▶│ cli.py ──▶ agent.py (loop) ──▶ llm/ ──────────────────────────────────┼──▶ chat model (LLM_PROVIDER:
                         │               │                                                       │    Anthropic, Azure AI Foundry,
                         │               │                                                       │    OpenAI, Ollama, ...)
                         │               ▼ tool calls                                            │
                         │          tools/registry.py                                            │
                         │      ┌────────┼──────────────────────┐                                │
                         │      ▼        ▼                      ▼                                │
                         │  mcp_tools  kubectl_tool        knowledge.py                          │
                         │      │        │ (fallback)        load_skill                          │
                         └──────┼────────┼───────────────────────────────────────────────────────┘
                                ▼        ▼
                 kubernetes-mcp-server   kubectl ─────▶ Kubernetes API (read-only)
                 (read_only, no Secrets)
```

In the cluster the same program runs in a pod (`k8s-servant serve`), and kubernetes-mcp-server
runs next to it as a **sidecar** container on `localhost:8080`.

## Components

| Module | What it does | Why it exists |
|---|---|---|
| `config.py` | Reads every setting from environment variables into one dict. Sets up logging to stderr. | The same code runs on a laptop and in a pod. Only the environment differs. |
| `llm/` | The model client package. `types.py` holds the neutral `Turn`, `ToolCall` and `ToolSpec` the agent speaks; `providers.py` the provider table (wire API, default endpoint and effort) and endpoint rules; `auth.py` resolves the sign-in (API key, Entra ID or none). One module per wire API converts the turns and sends one request: `responses.py` (Foundry, OpenAI), `messages.py` (Claude, on Anthropic or Foundry) and `chat_completions.py` (Ollama, OpenAI-compatible servers). `build_chat()` picks the client for `LLM_PROVIDER`; `model_error_text()` turns failures into advice without the API key. | It keeps everything specific to a provider, its API and its authentication in one place, so the agent never depends on a model SDK. |
| `tools/registry.py` | Defines the `Tool` shape (name, description, JSON schema, `run`), the `ToolRegistry` and `truncate_output()`. | The loop handles every tool the same way, wherever the tool comes from. |
| `tools/mcp_tools.py` | Connects to kubernetes-mcp-server and wraps each of its tools as a `Tool`. | It is the preferred way to read the cluster. |
| `tools/kubectl_tool.py` | A guarded `kubectl` that only allows read verbs and never touches Secrets. | It is the fallback when MCP is not available. |
| `tools/cluster_tools.py` | `cluster_problems` (only what is unhealthy, plus coverage) and `warning_events` (grouped Warning events, noisy reasons filtered). Fixed `kubectl get -o json` commands, checks in Python. | Large clusters do not fit into the model's context; see [Large clusters](#large-clusters-summary-tools-first). |
| `knowledge.py` | Loads the environment description, cluster notes and skills, and provides the `load_skill` tool. | It lets you teach the agent without changing code. |
| `agent.py` | Builds the system prompt and runs the tool-calling loop. | This is the "brain", about 100 lines that read top to bottom. |
| `tools/prometheus_tool.py` | `prometheus_query`, `prometheus_query_range` and `alertmanager_alerts`, all GET-only, registered only when their URLs are set. | Metrics and alerts show what the Kubernetes API cannot: closeness to limits, trends, timing. |
| `runtime.py` | Wires one session. `open_runtime()` registers the tools (MCP, kubectl, Prometheus, `load_skill`, `save_note`). `open_agent()` adds the system prompt with learned notes and the model client. | It keeps the wiring out of the CLI and the scheduler. |
| `findings.py` | The finding model, fingerprints, and the parser for the model's findings JSON. | Stable fingerprints make "new vs still open" possible. |
| `memory.py` | SQLite (findings, runs, meta) and `notes.md`, plus the `save_note` tool. | The model remembers nothing; the agent must. See [memory.md](memory.md). |
| `healthcheck.py` | One health check: prompt → agent → findings JSON (one retry) → memory diff → Markdown report. | One place that guarantees a broken answer never changes memory. |
| `report.py` | The morning report: fresh check, the night since the previous report from memory, alerts firing now and alerts that fired overnight, a model-written overview. | How the night went and what needs attention today, even when nothing changed. |
| `teams.py` | Adaptive Cards for checks and the morning report, shrunk to Teams' size limit, and posting to the Workflows webhook. | Keeps Teams details (format, limits, the URL as a secret) in one module. |
| `scheduler.py` | The `serve` loop: a check every `CHECK_INTERVAL_MINUTES`, the morning report when due, heartbeat, SIGTERM. | The agent's main process in the cluster. |
| `cli.py` | The `k8s-servant` command: `ask`, `check`, `report`, `serve`, `findings`, `history`, `notes`, `tools`, `doctor`. | It is how people use the agent. |

## How a question is answered

```
you            cli.py          agent.py            chat model           tools
 │ ask "..."     │                │                     │                 │
 │──────────────▶│ open_runtime   │                     │                 │
 │               │──(MCP connect, kubectl, load_skill)──────────────────▶│
 │               │ Agent.run()    │                     │                 │
 │               │───────────────▶│ system + question   │                 │
 │               │                │────────────────────▶│                 │
 │               │                │ tool call: load_skill("cluster-health-check")
 │               │                │◀────────────────────│                 │
 │               │                │──────────────────────────────────────▶│ skill text
 │               │                │◀──────────────────────────────────────│
 │               │                │ tool result          │                 │
 │               │                │────────────────────▶│                 │
 │               │                │ tool call: pods_list │                 │
 │               │                │◀────────────────────│                 │
 │               │                │  ... repeats until the model answers without tool calls
 │               │                │ final answer        │                 │
 │               │                │◀────────────────────│                 │
 │ answer (stdout)◀───────────────│                     │                 │
```

The loop step by step:

1. The messages start as `[system prompt, your question]`.
2. Send them to the model together with the definitions of every registered tool.
3. If the reply has **no tool calls**, it is the answer and the loop is done.
4. Otherwise, run each requested tool and append its output as a `tool` message. Large
   output is truncated first. Then go back to step 2.
5. After `MAX_ITERATIONS` rounds (default 25), ask the model one last time, with no
   tools, for the best answer it has so far. This stops a confused model from looping
   forever, and you still get a useful partial answer.

### The system prompt

`build_system_prompt()` assembles the prompt at the start of every run, in this order:

1. **Rules.** Read-only; evidence for every statement and never invent anything; copy
   object names exactly; check the skill catalog first; use the environment description
   and notes; save lasting facts; work on your own (never end with "if you want, I
   can…"); start health questions with the summary tools; keep tool output small; UTC;
   conclusion first.
2. **Live tools.** Which tool surfaces are available. If MCP is down, an explicit note
   says so.
3. **Environment description**, from `knowledge.environment`.
4. **Cluster notes**, from `knowledge.clusterNotes`.
5. **Learned notes**, the newest up to `NOTES_MAX_CHARS`.
6. **Skill catalog.** Only the names and descriptions. The model loads a full skill with
   `load_skill` when it needs one, so the prompt stays small as the number of skills
   grows.

On a typical platform this is about 10,000 characters (~2,500 tokens). The whole path from the
files in Git to the model, with an example: [how-the-agent-learns.md](how-the-agent-learns.md).

## Why our own loop and no framework

Agent frameworks (Microsoft Agent Framework, LangChain, AutoGen, CrewAI and others) wrap
this loop in their own classes and add memory, prompt templates, plugins and
multi-agent orchestration. We chose not to use one:

- **Transparency.** The loop is about 100 commented lines in `agent.py`. You can see
  exactly what goes to the model and what comes back.
- **Stability.** In 2026 the Python APIs of these frameworks changed several times;
  for example, Microsoft Agent Framework removed its Azure OpenAI classes. A project built
  on one can break on an upgrade.
- **Debugging.** When the agent misbehaves you read our log lines and our code, not a
  framework's internals.
- **Few dependencies.** The core runtime libraries are `openai`, `anthropic`,
  `mcp`, `pyyaml`, `httpx2` and `tzdata`; `azure-identity` is the optional `azure` extra
  (included in the image).

**What we give up.** We write the loop, output truncation and error handling ourselves,
which is a few dozen lines. If a later stage needs what a framework offers, such as
multi-agent work, only `agent.py` changes. The tools, knowledge and model client are
separate modules.

## Why three APIs

Newer reasoning models need their hidden thinking carried from one tool call to the
next, and the Chat Completions API has no place for it. The model client therefore
speaks three APIs behind one neutral interface (`k8s_servant/llm/`): the Responses API
(`foundry`, `openai`), Anthropic's Messages API (`anthropic`) and Chat Completions
(`ollama`, `openai-compatible`). `LLM_PROVIDER` decides which; the agent loop, health
check and report never see the difference. Switching models within a provider is a
configuration change (`LLM_MODEL`). Plain-words explanation and a recipe per provider:
[model-providers.md](model-providers.md).
We set no `temperature`, because reasoning models accept only the default.

## MCP first, kubectl as fallback

[kubernetes-mcp-server](https://github.com/containers/kubernetes-mcp-server) publishes
Kubernetes read operations as MCP tools, for example `pods_list`, `pods_log`,
`resources_list` and `nodes_top`. We chose it because:

- It runs in a cluster and speaks streamable HTTP. Microsoft's `aks-mcp`, by contrast,
  is stdio-only and is documented as not to be run in a cluster.
- `read_only = true` exposes only read tools, and `[[denied_resources]]` hides Secrets.
- Its tool descriptions are written for models.

The configuration is in [`deploy/local/mcp-config.toml`](https://github.com/k8s-servant/k8s-servant/blob/main/deploy/local/mcp-config.toml).
Only the `core` toolset is enabled:

- **`config` is off.** Its `configuration_view` tool returns the raw kubeconfig,
  including tokens and client keys, which must never reach the model.
- **`helm` is off.** Helm stores releases in Secrets, which are denied anyway.

**The fallback rule:**

| MCP server | Tools registered | What the model is told |
|---|---|---|
| reachable | MCP tools (without `events_list`), `kubectl` (description starts with `FALLBACK:`), `cluster_problems`, `warning_events` and `load_skill` | Start health questions with the summary tools; prefer MCP tools for single objects and logs; kubectl only when no MCP tool fits |
| unreachable or `MCP_URL` empty | `kubectl`, `cluster_problems`, `warning_events` and `load_skill` | "The Kubernetes MCP server is not available in this session" |

MCP's `events_list` is not offered to the model: it returns every event of every
namespace unfiltered, and on a large test cluster that exceeded the MCP client's 1 MB message
limit. `warning_events` covers the same need.

## Large clusters: summary tools first

The first run against a large test cluster (615 Deployments, 1,252 pods, 597 namespaces, 3,700
Warning events) showed that a model cannot read a large cluster object by object:
- the Deployment table alone was 78,000 characters, four times the output limit, so
  the model saw only the first part;
- the event list did not fit into one MCP message;
- the model reported "healthy" while alerts were firing.

So the broad part of a health check is done in Python, without the model:

| Tool | What it reads | What it returns |
|---|---|---|
| `cluster_problems` | Nodes, pods, Deployments, StatefulSets, DaemonSets, PodDisruptionBudgets, ResourceQuotas, PVCs, Jobs (`kubectl get -o json`, in parallel) | Only what is unhealthy, pods grouped by owning workload, plus how many objects were checked. 787 characters in 3.5 s on the test cluster. |
| `warning_events` | Warning events, with `EVENTS_EXCLUDE_REASONS` filtered out by the API server | Groups of (namespace, object, reason) with count, last time and latest message, most frequent first. On the test cluster, 3,550 of 3,710 Warning events were Kyverno `PolicyViolation` scans. |

The model then investigates each problem with MCP and kubectl, as the
`cluster-health-check` skill describes. Both tools run fixed commands; the model can
only choose a namespace, an event reason and a time window, and each is checked against
a strict pattern, so model text can never become a kubectl flag.

The check happens once per session (per CLI command, and per scheduled run).
A restarted sidecar is picked up on the next run.

### Why a sidecar

The MCP server runs as a second container in the agent's pod rather than as its own
Deployment:

- It is reached on `localhost` (`bind_address = "127.0.0.1"`), with no Service and no
  NetworkPolicy to manage.
- It uses the pod's ServiceAccount, so read-only RBAC is defined once.
- It starts and stops with the agent.

## Safety: read-only in layers

1. **Kubernetes RBAC.** This is the real security boundary. The agent's
   ServiceAccount is bound to the built-in `view` role and to its own cluster reader
   role (get/list/watch only); neither grants Secrets ([security.md](security.md#in-the-cluster)).
2. **The MCP server** in `read_only` mode, with Secrets denied.
3. **The kubectl guard.** Only read verbs, no Secrets, and only **allow-listed flags**,
   parsed the way kubectl parses them (`-n x`, `-nx`, `-n=x`). `-o` accepts only
   `wide`, `yaml`, `json`, `name`, `jsonpath=` and `custom-columns=`. A deny-list is not
   enough here: kubectl has many flags that send its token to another server (`-s`,
   `--server`), read local files (`-o go-template-file=...` would print the
   ServiceAccount token) or never return (`--follow`, `--watch`). It never uses a shell.
4. **The prompt.** It tells the model it is read-only and should describe fixes, not
   apply them.

Layers 2–4 keep the agent from wasting turns and make the intent visible. Layer 1
would hold even if they all failed. See [security.md](security.md) for what data
leaves the cluster.

## Errors never stop an investigation

- `ToolRegistry.run()` never raises. These all come back to the model as text that
  starts with `ERROR:`, so it can react, for example by trying the kubectl fallback:
  - an unknown tool;
  - invalid JSON arguments;
  - a tool that crashes;
  - kubectl exiting with a non-zero code or timing out;
  - an MCP tool reporting an error, or an MCP call failing (too large an answer, a
    timeout). The text then tells the model to narrow the query or use another tool.
- Model-call failures (401, 403, 404, 429, network, token) stop the command with an
  explanation from `model_error_text()`, and the exit code is 1.

## Output truncation

Tool output longer than `TOOL_OUTPUT_MAX_CHARS` (default 8,000) is cut. Every later model call resends it, so this limit drives the token cost. The **head**
(two thirds) and the **tail** (one third) are kept, with a marker in between that tells
the model to narrow the query. The head of `kubectl get` has the columns and the first
rows; the tail of a log has the latest lines.

## Scheduled checks and the morning report

`k8s-servant serve` is the agent's main process in the cluster:

```
Scheduler.run  (loop, at least once a minute; touches HEARTBEAT_FILE)
 ├─ morning report due?  (MORNING_REPORT_TIME in MORNING_REPORT_TIMEZONE, weekdays, not sent today)
 │    └─ report_and_post ─▶ open_agent ─▶ build_morning_report
 │          ├─ run_check (a fresh health check, see below)
 │          ├─ memory: open findings; new / resolved / came and went since the previous
 │          │          report (Monday: the weekend; max 72 h); checks full / skipped / failed
 │          ├─ Alertmanager: alerts firing now; Prometheus: alerts that fired and stopped
 │          ├─ model (no tools): a short overview from these facts only
 │          └─ morning_card ─▶ Teams   (unless TEAMS_POST_MODE=off)
 └─ next health check due?  (every CHECK_INTERVAL_MINUTES)
      └─ check_and_post ─▶ open_agent ─▶ run_health_check
            ├─ cluster_problems + alertmanager_alerts, without the model (a few seconds)
            │    identical to the last full check (and it is < CHECK_FULL_INTERVAL_HOURS old)?
            │    → recorded as "unchanged", open findings kept, nothing posted. Otherwise:
            ├─ Agent.run("follow cluster-health-check; open findings: ...")
            ├─ parse_findings(answer)   ── invalid? one follow-up; still invalid → failed run, memory untouched
            ├─ Memory.apply_check()  ─▶ new / still open / resolved
            ├─ render_check_report() ─▶ stored in history, printed to the log
            └─ check_card ─▶ Teams   (changes: only when something is new or resolved)
```

What happens when something fails:
- **One failing run** (model timeout, network glitch) is logged and recorded in the
  history. The loop continues and the next run happens on time.
- **A stuck run** is cancelled after `JOB_TIMEOUT_MINUTES`. The heartbeat is touched
  by a background task while a run is in progress, so a long check does not trip the
  liveness probe.
- **Shutdown (SIGTERM)** cancels the running check or report at once. If that was the
  morning report, that day's report is lost: its date was marked before it ran, so that
  a failing report is not retried every minute. The log says so.
- **A health check whose answer has no valid findings** is recorded as failed and
  does not change memory. Otherwise every open problem would be marked resolved.
- **The morning report always goes out.** If its check or the overview fails, it is
  built from memory and says what failed: a silent morning would look like "all is
  fine". The date is marked before the report runs, so a report that keeps failing
  is not retried every minute.
- **Teams being down** never fails a check. The post failure is logged, without the
  URL.
- **Restarts.** A restart re-runs a check right away. The morning report is not sent
  twice on the same day, because the date it was last sent is kept in memory.

Each run opens a fresh session (MCP connection, tools, system prompt with the newest
learned notes), so a restarted MCP sidecar or a new note is picked up on the next run.

## Logging

All logs go to **stderr**, so stdout carries only the answer (`k8s-servant ask ... > answer.md`).

| Level | What you see |
|---|---|
| INFO | Knowledge loaded, MCP connection and tool count, every model call with its prompt, completion and reasoning tokens, every tool call with arguments, output size and duration, each kubectl command line, check results (new/open/resolved), notes saved, Teams posts and decisions not to post |
| WARNING | MCP unreachable, kubectl calls refused by the guard, skills without a description, MAX_ITERATIONS reached, invalid findings JSON (before the retry), Teams webhook not configured |
| ERROR | Failed health check, failed Teams post (without the URL), failed morning overview, a scheduled job that raised (with traceback) |
| DEBUG | Full tool outputs and tracebacks of failing tools |

The libraries' own HTTP logging is kept at WARNING.
