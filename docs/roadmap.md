# Roadmap

What exists today, what comes next, and ideas for later. What changed release by
release is in the [release notes](https://github.com/k8s-servant/k8s-servant/releases). Ideas and votes are welcome as GitHub
issues.

## Available today

| Area | What exists |
|---|---|
| **Core agent** | The tool-calling loop on an Azure AI Foundry chat model; MCP tools with a guarded kubectl fallback; knowledge (environment description, cluster notes, skills); CLI `ask`, `tools`, `doctor`; Foundry guide and `scripts/setup-foundry.sh` |
| **Checks, memory, Teams** | Scheduled health checks with findings memory (new / still open / resolved), the morning report, Teams cards, Prometheus and Alertmanager tools; `cluster_problems` / `warning_events` summary tools, the policy-event filter, reasoning effort, skipping unchanged checks |
| **In the cluster** | Signed container image, Helm chart as an OCI artifact (agent + MCP sidecar, read-only RBAC, PVC, credentials from your own Secrets, extension points), examples for Azure, any cluster and Flux, `scripts/setup-workload-identity.sh`, the [deployment guide](deployment.md) |

## Direction

The project's direction is written down in the
[vision](superpowers/specs/2026-10-17-teammate-vision-design.md): k8s-servant should
feel like **one more person on the team**. It explains what is wrong, why, and how to
fix it; it shows up on its own and remembers; it works where the team already is; and
it earns the right to do more through a visible track record. Being read-only stays the
default and the core.

## Next, in order

| Step | What | Issue |
|---|---|---|
| 0 | Multi-provider LLM support (Anthropic, OpenAI, Ollama, ...; Azure AI Foundry stays the main example) and provider-neutral docs | [#2](https://github.com/k8s-servant/k8s-servant/issues/2), [#11](https://github.com/k8s-servant/k8s-servant/issues/11) |
| 1 | **Two-way MS Teams bot for the whole fleet**: @mention questions, interactive cards, platform-team allow-list, per-day budget (opt-in component) | [#27](https://github.com/k8s-servant/k8s-servant/issues/27) |
| 2 | **Scoreboard**: answers graded by the asker, the platform team and the bot's own verification; accuracy per task type; the offline evaluation set | [#28](https://github.com/k8s-servant/k8s-servant/issues/28) |
| 3 | **Cost meter and budgets** | [#29](https://github.com/k8s-servant/k8s-servant/issues/29) |
| 4 | **Action runbooks with a trust ladder**: explain, then propose and approve, then do and report; first a pod restart, then rolling node restarts after patching; a separate action component | [#30](https://github.com/k8s-servant/k8s-servant/issues/30) |
| 5 | **Developer self-service** with per-person scoping, through the developer portal and chat | [#31](https://github.com/k8s-servant/k8s-servant/issues/31) |
| 6 | **Make existing strengths known**: memory, skip-unchanged (benchmarked), the morning report | [#32](https://github.com/k8s-servant/k8s-servant/issues/32) |

On their own tracks, not blocking the order above: Slack and other notification
channels ([#3](https://github.com/k8s-servant/k8s-servant/issues/3),
[#4](https://github.com/k8s-servant/k8s-servant/issues/4)), Key Vault references
([#20](https://github.com/k8s-servant/k8s-servant/issues/20)), the documentation site
([#5](https://github.com/k8s-servant/k8s-servant/issues/5)) and CI/image hardening
([#6](https://github.com/k8s-servant/k8s-servant/issues/6),
[#7](https://github.com/k8s-servant/k8s-servant/issues/7),
[#8](https://github.com/k8s-servant/k8s-servant/issues/8),
[#18](https://github.com/k8s-servant/k8s-servant/issues/18)).

## Ideas for later

| Idea | Why |
|---|---|
| A log-search MCP server (for example Elasticsearch or Loki) | Logs older than the pod's tail and across pods |
| Read-only cloud-provider access through Workload Identity (for example the Azure Activity Log, the AKS resource, the node resource group) | Explain node pool, upgrade and network problems that the Kubernetes API cannot show |
| Redaction of tool output before the model | Only if logs turn out to carry personal data or credentials ([data-processing.md](data-processing.md#what-is-not-done)) |
| A fair comparison with HolmesGPT on the evaluation set | Show where each tool is stronger, with numbers |
