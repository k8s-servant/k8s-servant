# FAQ and limitations

**Can it break something?** No. Every tool only reads; the cluster's RBAC
gives it read-only roles without Secrets; kubectl is limited to an allow-list; there
is no shell. See [security.md](security.md).

**What data leaves the cluster?** Object names, statuses, events, metrics and, when
it investigates, log lines go to the model endpoint (with Microsoft Foundry: in the data
zone you choose, not used for training; with other providers see their data policies,
linked from [model-providers.md](model-providers.md#data-handling-and-errors)). Findings and summaries go to Teams. Never
Secrets. Most data is reduced in Python first; log lines are not redacted.
[security.md](security.md#what-data-leaves-the-cluster),
[data-processing.md](data-processing.md).

**Use with care.** k8s-servant gives advice; it does not act. Its answers come from a
language model and can be wrong or incomplete, so check them before you change anything
in a cluster. You decide which model provider your cluster data goes to
([data processing](data-processing.md)), what RBAC the agent gets, and who can
read its reports. The software is provided "as is", without warranty, under the
[Apache-2.0 license](https://github.com/k8s-servant/k8s-servant/blob/main/LICENSE).

**Why does a check sometimes take a minute and sometimes 6 seconds?** Six seconds
means nothing changed since the last full check, so the model was skipped.

**Why did it not post anything?** With `TEAMS_POST_MODE=changes` a check is posted only
when something is new or resolved. The morning report is always posted.

**It reports something that is normal here.** Add it to the environment description
or cluster notes, or let it save a learned note. See
[Teaching the agent](knowledge-and-skills.md).

**Can I use another model?** Yes, any chat model with tool calling: change `LLM_MODEL`,
or `LLM_PROVIDER` to use OpenAI, Anthropic, Ollama or any OpenAI-compatible server
(`LLM_ENDPOINT` is then the full base URL including its path). For models without
reasoning set `LLM_REASONING_EFFORT=` (empty). Recipes:
[model-providers.md](model-providers.md).

**Does it need AKS?** No. The agent and the chart work on any Kubernetes cluster. The
Workload Identity and Key Vault parts are optional and AKS-specific.

**Why not an agent framework or HolmesGPT?** A small own loop is easy to read, test
and secure. [HolmesGPT](https://github.com/HolmesGPT/holmesgpt) covers much of the same
ground and is worth a look if it fits you better. See [architecture.md](architecture.md#why-our-own-loop-and-no-framework).

## Known limitations

Honest notes from the first real runs:

- **Run-to-run variation.** The model can judge the same evidence differently in two
    runs (`azure-cns` restarts were "healthy now" once and "warning" the next time). A
    finding can then appear and disappear in Teams. Explicit rules in the environment
    description reduce this.
- **Name mix-ups.** It once reported a healthy workload whose name differed by one
    character from the broken one. The prompt now asks for exact names; check the
    evidence before acting.
- **Detection comes from rules, explanation from the model.** On its own the model
    missed firing alerts on a large cluster; `cluster_problems` (plain Python checks) is
    what makes detection reliable. Problems outside those checks depend on the model
    noticing them.
- **No measure of answer quality yet.** Tests prove the mechanics, not that the agent
    finds the right problems. An evaluation set (saved cluster states with expected
    findings) is planned.
- **The summary cannot see everything** (Flux, node usage, certificates), so skipped
    checks rely on a full check at least every 6 hours.
- **One cluster per process**, single replica; memory is SQLite on a 1 Gi volume.
- **Clusters that are recreated regularly** lose their memory with the volume, and a
    new OIDC issuer needs the federated credential re-pointed
    ([deployment.md](deployment.md#when-the-cluster-is-recreated)).
- **Model quality varies by provider and model.** All five providers are supported
    ([model-providers.md](model-providers.md)), but small self-hosted models find
    fewer problems than current hosted reasoning models. Bedrock and Vertex AI sign-in
    are not supported yet.
- **Notifications go to MS Teams only.** Slack and other channels are planned
    ([#3](https://github.com/k8s-servant/k8s-servant/issues/3),
    [#4](https://github.com/k8s-servant/k8s-servant/issues/4)).

## Cost and limits

With a hosted model you pay **per token**; an idle agent costs nothing, and a local
Ollama costs only your hardware. Measured on a test cluster (~600 Deployments, ~1,250
pods) with gpt-5.4-mini on Microsoft Foundry; other models use a similar number of
tokens, at their own price:

| Run | Model calls | Tokens | Time |
|---|---|---|---|
| Full health check | ~10 | 150k–225k (mostly input; ~85% of it served from the prompt cache, billed at a discount) | 45–80 s |
| Scheduled check, nothing changed | 0 | **0** | ~6 s |
| `ask` | 3–10 | depends on the question (not measured separately; less than a full check) | 10–60 s |
| Morning report | a full check + 1 overview call | a full check plus a small overview call | ~1–2 min |

- **Rate limits.** A full check's busiest minute uses about 90k tokens, so your
    provider's tokens-per-minute limit must cover that, or deep checks fail with
    `HTTP 429`. On Microsoft Foundry, give the deployment 150–200k TPM (raising it costs
    nothing).
- **Monthly cost** depends on how often something changes, because unchanged hours
    are free. For gpt-5.4-mini, a rough estimate from public price lists was about
    $0.10–0.20 per full check, so from roughly $10–30 a month per cluster when most hours
    are quiet to $70–160 if every hour ran in full. Prices change and differ by model and
    region: check your provider's current price list.
- **Levers:** `CHECK_SKIP_UNCHANGED`, `CHECK_FULL_INTERVAL_HOURS`,
    `CHECK_INTERVAL_MINUTES`, `TOOL_OUTPUT_MAX_CHARS`, `LLM_REASONING_EFFORT`.
- Every model call is logged with its tokens: `model call: N prompt (M cached) + K
    completion tokens (R reasoning)`. Set a budget alert with your provider (on Foundry: the resource group).

More: [model-providers.md](model-providers.md); for Microsoft Foundry,
[Foundry setup guide](providers/foundry/setup.md#9-cost-and-limits) and
[Foundry concepts](providers/foundry/concepts.md).
