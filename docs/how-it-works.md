# How it works

```
 you ──▶ k8s-servant ask / check / report ──┐
 scheduler (serve): hourly + 08:00 ─────────┤
                                            ▼
              scheduled check: summary tools first, without the model
              (cluster_problems + alerts) ── unchanged? ──▶ "unchanged", done (0 tokens)
                                            │ changed / manual / every 6 h
                                            ▼
                                       agent loop ──────▶ chat model (your LLM_PROVIDER)
                                       │  ▲                (e.g. gpt-5.4-mini, reasoning medium)
                           tool calls  ▼  │ results (cut to 8,000 chars)
 ┌──────────────────┬───────────────┬──────────────┬──────────────┬───────────┬──────────┐
 cluster_problems   kubernetes-mcp  kubectl         Prometheus /   load_skill  save_note
 warning_events     server          (fallback,      Alertmanager   (skills)    (notes)
 (summaries)        (read-only)     allow-listed)
 └──────────────────┴───────┬───────┘
                            ▼
                Kubernetes API (read-only; Secrets never read)

 memory (DATA_DIR): findings (new / still open / resolved), run history, learned notes
 results ──▶ stdout, history, MS Teams (changes after a check; the morning report)
```

1. A question (or "run the health check skill") and a system prompt go to the model.
      The system prompt holds the rules, the environment description, cluster notes,
      learned notes and the skill catalog.
2. The model asks for tools. For health it starts with the **summary tools**:
      `cluster_problems` returns only what is unhealthy across the whole cluster, and
      `warning_events` returns grouped Warning events without policy-scan noise. Then it
      follows each lead with MCP tools and kubectl (describe, events, logs, metrics).
3. The agent runs the tools, cuts large outputs down and sends the results back. This
      repeats until the model answers; at most 25 rounds.
4. A health check ends with a JSON list of findings. Memory compares them with the
      previous check (new / still open / resolved) and decides what to post.
5. Scheduled checks first compare the summary with the last full check; if nothing
      changed, the model is not called at all.

There is no agent framework: the loop is about 100 commented lines in
`k8s_servant/agent.py`. Why, and the rest of the design:
[architecture.md](architecture.md). Every tool: [tools.md](tools.md).

## Repository structure

```text
k8s-servant/
├── k8s_servant/                    # The agent (Python package, `k8s-servant` command)
│   ├── cli.py                      # Commands: ask, check, report, serve, findings, history, notes, tools, doctor
│   ├── config.py                   # Every setting, from environment variables; logging
│   ├── agent.py                    # System prompt + tool-calling loop
│   ├── llm/                        # Model client
│   │   ├── types.py                # Neutral Turn, ToolCall, ToolSpec; the agent depends only on these
│   │   ├── providers.py            # The provider table (wire API, default endpoint and effort) and endpoint rules
│   │   ├── auth.py                 # LLM_AUTH: API key, Entra ID or none; Entra tokens only for Foundry endpoints
│   │   ├── errors.py               # Model errors and their actionable, key-free messages
│   │   ├── chat_completions.py     # Chat Completions client: reasoning effort, token log
│   │   ├── responses.py            # Responses API client: stateless, encrypted reasoning carried between calls
│   │   └── messages.py             # Messages API client for Claude: signed thinking and prompt caching
│   ├── runtime.py                  # Wires tools, prompt and model for one run
│   ├── knowledge.py                # Environment description, cluster notes, skills; load_skill tool
│   ├── healthcheck.py              # One health check; skipping the model when nothing changed
│   ├── findings.py                 # Findings, fingerprints, parsing the model's JSON
│   ├── memory.py                   # SQLite memory (findings, runs, meta) + learned notes; save_note tool
│   ├── report.py                   # The morning report
│   ├── teams.py                    # Adaptive Cards, posting to the Teams Workflows webhook
│   ├── scheduler.py                # `serve`: hourly checks + the morning report, heartbeat, SIGTERM
│   ├── scan/
│   │   ├── facts.py                # Scan facts: evidence, certainty, scope; scan results
│   │   └── inputs.py               # Scan inputs read once each; Prometheus and Alertmanager access
│   └── tools/
│       ├── registry.py             # Common Tool shape, registry, output size cap
│       ├── cluster_tools.py        # cluster_problems + warning_events: summaries for large clusters
│       ├── mcp_tools.py            # Tools from kubernetes-mcp-server
│       ├── kubectl_tool.py         # Guarded read-only kubectl (allow-listed verbs and flags)
│       └── prometheus_tool.py      # PromQL, Alertmanager, alert history
├── charts/k8s-servant/             # Helm chart: agent + MCP sidecar, RBAC, PVC (values.yaml: every key commented)
│   ├── Chart.yaml                  # version = appVersion = image version
│   ├── values.yaml                 # Defaults
│   ├── templates/                  # Deployment, ConfigMaps (settings, knowledge, MCP), RBAC, PVC, extraObjects
│   └── knowledge/skills/           # Built-in generic skills, one SKILL.md each (rendered into the knowledge ConfigMap)
│       ├── cluster-health-check/
│       ├── workload-troubleshooting/
│       ├── node-troubleshooting/
│       ├── flux-gitops-status/
│       └── prometheus-queries/
├── examples/
│   ├── knowledge-values.yaml       # Example environment description and cluster notes for a fictional platform
│   ├── values-azure.yaml           # The complete Azure setup: Foundry + Workload Identity + Key Vault via External Secrets
│   ├── values-azure-csi.yaml       # Azure without Kubernetes Secrets: Key Vault mounted by the CSI driver
│   ├── values-azure-claude.yaml    # Claude on Foundry with Workload Identity
│   ├── values-anthropic.yaml       # Claude from Anthropic, key from a Secret
│   ├── values-openai.yaml          # OpenAI, key from a Secret
│   ├── values-ollama.yaml          # An in-cluster Ollama, no key
│   ├── values-minimal.yaml         # Any cluster: OpenAI-compatible endpoint and Teams webhook from kubectl-created Secrets
│   └── flux-helmrelease.yaml       # Flux HelmRelease installing the OCI chart
├── deploy/local/
│   └── mcp-config.toml             # kubernetes-mcp-server config for local runs (read-only, core toolset)
├── scripts/
│   ├── setup-foundry.sh            # Create the Foundry resource + model deployment, grant your role
│   └── setup-workload-identity.sh  # The pod's managed identity: federated credential, Key Vault secret + Foundry roles
├── tests/                          # pytest suite: one file per module, fakes.py, documentation checks
├── docs/                           # Documentation (see Documentation below)
│   └── images/                     # Logo (light and dark), icon, avatar and social preview
├── .github/                        # CI (GitHub Actions), release-please, issue and PR templates
├── Dockerfile                      # Image: Python alpine, pinned kubectl, non-root, read-only root fs
├── pyproject.toml                  # Package, dependencies, the `k8s-servant` entry point
├── .env.example                    # Template for local settings (no secrets)
├── CHANGELOG.md                    # What changed (generated by release-please)
├── CONTRIBUTING.md                 # How to contribute
├── AGENTS.md                       # Conventions for AI coding assistants
└── CLAUDE.md                       # Imports AGENTS.md for Claude Code
```
