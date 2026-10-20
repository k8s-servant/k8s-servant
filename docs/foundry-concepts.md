# MS Foundry concepts

This page explains what Microsoft Foundry is, how its parts fit together, and which of
them k8s-servant uses. Read it before [foundry-setup.md](foundry-setup.md), which is
the step-by-step runbook.

## Contents

- [What Foundry is](#what-foundry-is)
- [How it fits together](#how-it-fits-together)
- [The model catalog](#the-model-catalog)
- [Deployment types: where data is processed](#deployment-types-where-data-is-processed)
- [Capacity and quota](#capacity-and-quota)
- [Cost](#cost)
- [The API: how the agent calls a model](#the-api-how-the-agent-calls-a-model)
- [Access: two separate layers](#access-two-separate-layers)
- [Network](#network)
- [Safety and privacy](#safety-and-privacy)
- [Shared or dedicated resource](#shared-or-dedicated-resource)
- [What the agent uses](#what-the-agent-uses)

## What Foundry is

**Microsoft Foundry** (formerly *Azure AI Foundry*, and before that *Azure AI Studio*)
is Azure's platform for hosting and using AI models. It is two things:

1. **An Azure resource** that hosts models behind an API. This is what the agent uses.
2. **A web portal**, [ai.azure.com](https://ai.azure.com), to manage that resource and
   build on it: chat playground, agent builder, evaluations, prompt tools. The agent
   uses none of these, but the playground is handy for trying a model by hand.

**Foundry vs Azure OpenAI.**
- An older **Azure OpenAI** resource (kind `OpenAI`) hosts only OpenAI models.
- A **Foundry resource** (kind `AIServices`) hosts OpenAI models and other providers'
  models, and also includes Speech, Vision and Language.
- New work should use a Foundry resource.

## How it fits together

```
Subscription
└── Resource group
    └── Foundry resource  (e.g. my-foundry)                  ← endpoint, network rules, keys, roles
        ├── Deployment "gpt-5"          (gpt-5, DataZone, 150k TPM)
        ├── Deployment "k8s-servant"  (gpt-5.4-mini, DataZone, 50k TPM)   ← the agent's own
        └── Projects (optional)                              ← portal workspaces: agents, evals, files
```

| Concept | What it is | Analogy |
|---|---|---|
| **Resource** | The Azure object you pay for and secure. It has an endpoint (URL), network access rules, role assignments and keys. | A database server |
| **Model** | An entry in the catalog, e.g. `gpt-5.4-mini` version `2026-03-17`. | A software product |
| **Deployment** | A named, running instance of a model on your resource, with its own throughput limit. **You always call a deployment by its name**, never the model directly. | A database on that server |
| **Project** | A workspace in the portal for agents, evaluations and datasets. | A team folder. The agent does not need one |

**Why deployments matter.**
- A model cannot be called until it is deployed on a resource.
- Each deployment has its own name, model version and capacity.
- Two teams can use the same model through two deployments without competing for each
  other's throughput. That is what a "dedicated model" means in Foundry: a dedicated
  **deployment**.

## The model catalog

- **Sold directly by Azure.** OpenAI's GPT family and selected models from other
  providers, hosted and billed by Microsoft under Azure's data-privacy terms. The
  agent's recommended `gpt-5.4-mini` is one of these.
- **Partner and community models.** For example Llama, Mistral and DeepSeek. Some work
  like the above; others are billed through Azure Marketplace.
- **Lifecycle.** Models have versions and retirement dates. For example, `gpt-4.1` is
  closed to new deployments, `gpt-5` retires in Feb 2027 and `gpt-5.4-mini` in Sep 2027.
  Expect to move to a newer version about once a year. That changes the deployment, not
  the agent's code. See the
  [retirement schedule](https://learn.microsoft.com/azure/foundry/openai/concepts/model-retirement-schedule).

## Deployment types: where data is processed

| Type | Where requests are processed | Typical use |
|---|---|---|
| **GlobalStandard** | Any Azure datacenter worldwide | Highest quota; new models arrive here first |
| **DataZoneStandard** | Only inside the EU data zone (for a resource in an EU region) | **The agent's choice:** cluster data stays in the EU |
| **Standard** (regional) | Only the resource's own region | Strictest residency, lower quota |
| *Provisioned (PTU)* | Reserved capacity at a fixed monthly price | High, steady volume. Not needed here |

Data at rest stays in the resource's region for every type. The types differ only in
where requests are **processed**.

## Capacity and quota

- **TPM (tokens per minute).** Every deployment gets a capacity; for example `50` means
  50,000 tokens per minute. Going over it returns HTTP 429, and the agent's SDK retries
  automatically.
- **Quota.** The subscription has a total TPM per model, region and deployment type.
  **All deployments of that model in that region draw from the same pool**, including
  other teams' deployments in the same subscription.
- **Capacity is not cost.** It only limits speed. An idle deployment costs nothing.

## Cost

- **Pay per token.** Input and output tokens are priced separately, per million tokens.
  The internal "thinking" tokens of reasoning models are billed as output. Prices are
  on the [pricing page](https://azure.microsoft.com/pricing/details/azure-openai/).
- **Rough scale for the agent.** One question is about 100k–300k tokens, because the
  conversation is resent on every round. An hourly health check is up to 24
  such runs a day. Mini models keep this small.
- **Billing granularity.** Cost reports show the **resource**, not individual
  deployments. On a shared resource the agent's cost is mixed with everyone else's.
- **Budgets.** Cost Management can alert on a budget but does not stop spending.

## The API: how the agent calls a model

- **The v1 API** at `https://<resource>.openai.azure.com/openai/v1/` is OpenAI-compatible.
  The standard `openai` Python SDK works unchanged; only the base URL differs. No
  `api-version` parameter is needed.
- **Stateless.** Every request carries the whole conversation; the model remembers
  nothing between requests. That is why:
  - the agent has its own memory;
  - long investigations get more expensive towards the end.
- **Tool calling.** The agent sends descriptions of its tools along with the
  conversation. The model answers either with text, or with a request such as "call
  `pods_list` with these arguments". The agent runs the tool, sends the result back,
  and repeats. That loop is the whole agent; see [architecture.md](architecture.md).

## Access: two separate layers

| Layer | Controls | Example roles | Who needs it |
|---|---|---|---|
| **Control plane** (Azure Resource Manager) | Creating and deleting the resource and deployments, network settings, keys | Owner, Contributor, Cognitive Services Contributor | Whoever creates the deployment |
| **Data plane** (the model API) | Calling models | **Cognitive Services OpenAI User** (OpenAI models only); Cognitive Services User or Foundry User (broader) | You, for local tests; the agent's identity in the cluster |

- **Owner and Contributor cannot call models.** An Owner can, however, assign the
  data-plane role to themselves. Role assignments take about 5 minutes to apply.
- **Roles apply to the whole resource.** Whoever has the data-plane role can call
  **every** deployment on that resource. Azure does not scope this role to a single
  deployment.
- **How callers authenticate:**
  - **Entra ID** (recommended): `az login` on a laptop, Workload Identity in the
    cluster. There is no secret to store or rotate.
  - **API keys**: two per resource. Anyone holding one can call every deployment.
    Acceptable for quick tests; shared resources often disable keys altogether.

## Network

- A resource can be **public** (reachable from the internet, still requiring
  authentication) or **private** (public access disabled, reached through a **private
  endpoint**, which is an IP address in your VNet with matching private DNS).
- If the cluster is private or sends egress through a firewall, the agent pod needs
  either of these to reach the resource:
  - a private endpoint, or
  - a firewall rule for the resource's hostname.

  See [foundry-setup.md, Step 5](foundry-setup.md#7-step-5-networking).

## Safety and privacy

- **Content filters.** Every deployment filters hate, sexual, violence and self-harm
  content and detects jailbreak attempts. Normal cluster data does not trigger them.
- **Privacy.**
  - Prompts and answers are not used to train models and are not shared with OpenAI.
  - Microsoft may keep content flagged for abuse for review, inside the EU for an EU
    resource. See
    [data privacy](https://learn.microsoft.com/azure/foundry/responsible-ai/openai/data-privacy).
- **What the agent sends.** Object names, statuses, events and log lines; never
  Secrets. Application logs can still contain sensitive data if applications log it.

## Shared or dedicated resource

The agent can use its own Foundry resource, or its own **deployment** on a resource
shared with other teams.

| | Own deployment on a shared resource | Dedicated resource |
|---|---|---|
| Setup | One `deployment create` plus a role assignment | Create the resource, deployment, roles and network |
| Throughput | Own capacity, but drawn from the shared subscription quota | Own capacity and, in its own subscription, its own quota |
| Cost visibility | Mixed with other deployments on the bill | Separate line on the bill |
| Access isolation | None: the data-plane role covers all deployments on the resource | Full |
| Network (private endpoint, firewall) | A change every user of the resource shares; agree it with the owners | Under your control |
| Keys | Shared with all users of the resource | Own; can be disabled freely |

**Recommendation: dedicated resources when the agent runs in private clusters.** It
then needs network rules, access and cost reporting of its own, which a shared resource
cannot give without affecting other teams. An own deployment on a shared resource is
fine for quick experiments. Switching between the two only changes `LLM_ENDPOINT`
and `LLM_MODEL`.

### Layout: one resource for non-prod, one for prod

| | Non-prod | Prod |
|---|---|---|
| Used by | dev and test clusters | production clusters |
| Subscription | your non-prod subscription | your prod subscription |
| Resource group | `my-foundry-nonprod-rg` | `my-foundry-prod-rg` |
| Resource | `my-foundry-nonprod` | `my-foundry-prod` |
| Region | an EU region such as West Europe or Sweden Central, if data must stay in the EU | same |
| Deployment | `k8s-servant`: gpt-5.4-mini, DataZoneStandard, capacity 50 (raise to 150–200), upgrade policy *once current version expired* | same |
| Private endpoint (optional, if egress is not allowed) | non-prod VNet | prod VNet |

Why two resources:
- **Production data stays in production.** Prod cluster data only reaches a resource
  in the production subscription, with its own access list.
- **Separate quota, policies and cost.** Quota is per subscription, so experiments
  cannot use up prod's throughput, and the bill shows prod and non-prod apart.
- **Network.** Each resource gets its private endpoint in the matching network, with
  the same routing and DNS as everything else there.
- **Safe model upgrades.** A new model version goes to the non-prod deployment first,
  then to prod, in the same order as other upgrades.

Why not one per cluster: non-prod clusters usually share a subscription and network
already, so a resource per cluster adds work without real separation. Recreating a
cluster does not affect the Foundry resource; only the agent's federated credential
changes ([deployment.md](deployment.md#when-a-cluster-is-recreated)).

One deployment can serve several non-prod clusters. Add a second only if the agents
start competing for throughput.

Names are examples. The custom subdomain must be globally unique, so confirm
availability when creating the resource.

Costs:
- the resource and an idle Standard-type deployment cost nothing; you pay per token;
- set a free budget alert on each resource group when you create it;
- a private endpoint, if one is needed, adds a small hourly network charge.

## What the agent uses

| Used | Not used |
|---|---|
| The resource's v1 endpoint | Projects |
| One deployment of its own | Foundry Agent Service (Microsoft-hosted agents) |
| Entra ID with *Cognitive Services OpenAI User* | Evaluations, prompt flow, fine-tuning |
| Default content filters | Provisioned throughput (PTU) |

**Next:** [foundry-setup.md](foundry-setup.md), the step-by-step setup.
