# Roadmap

What exists today, what comes next, and ideas for later. What changed release by
release is in the [CHANGELOG](../CHANGELOG.md). Ideas and votes are welcome as GitHub
issues.

## Available today

| Area | What exists |
|---|---|
| **Core agent** | The tool-calling loop on an Azure AI Foundry chat model; MCP tools with a guarded kubectl fallback; knowledge (environment description, cluster notes, skills); CLI `ask`, `tools`, `doctor`; Foundry guide and `scripts/setup-foundry.sh` |
| **Checks, memory, Teams** | Scheduled health checks with findings memory (new / still open / resolved), the morning report, Teams cards, Prometheus and Alertmanager tools; `cluster_problems` / `warning_events` summary tools, the policy-event filter, reasoning effort, skipping unchanged checks |
| **In the cluster** | Signed container image, Helm chart as an OCI artifact (agent + MCP sidecar, read-only RBAC, PVC, Teams Secret through External Secrets), examples for Azure and Flux, `scripts/setup-workload-identity.sh`, the [deployment guide](deployment.md) |

## Next

1. **An evaluation set.** Saved cluster states (the summary tools' output, events,
   alerts) with the findings a person expects. It runs after every change to the
   prompt, a skill, the summary tools or the model, and measures how often the agent
   finds the right problems and how much each run varies.
2. **Multi-provider LLM support (OpenAI, Anthropic, Ollama).** A provider setting and
   provider-neutral variable names, with Azure AI Foundry as one option among several.
   Today other OpenAI-compatible endpoints work only experimentally, through
   `FOUNDRY_ENDPOINT` and `FOUNDRY_API_KEY`.
3. **A comparison with HolmesGPT** on the same evaluation set.
4. **Notification targets beyond MS Teams** (for example Slack or a generic webhook).

## Ideas for later

| Idea | Why |
|---|---|
| A small HTTP API (`/ask`, `/check`, `/findings`), with the CLI as a thin client | Ask the agent without `kubectl exec`; the base for a chat bot |
| A Teams `@agent` bot, built on the HTTP API | Ask questions where the reports already arrive |
| A log-search MCP server (for example Elasticsearch or Loki) | Logs older than the pod's tail and across pods |
| Read-only cloud-provider access through Workload Identity (for example the Azure Activity Log, the AKS resource, the node resource group) | Explain node pool, upgrade and network problems that the Kubernetes API cannot show |
| Redaction of tool output before the model | Only if logs turn out to carry personal data or credentials ([data-processing.md](data-processing.md#what-is-not-done)) |
| Several clusters per process | One agent for a small fleet, with memory per cluster |
