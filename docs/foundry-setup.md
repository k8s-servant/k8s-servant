# MS Foundry setup

This runbook sets up the language model the agent uses: a chat model deployed in
**Microsoft Foundry** (formerly Azure AI Foundry), which the agent calls over HTTPS.
Follow the steps in order. Each step explains what you are doing and why, and gives
both the portal path and the `az` CLI commands.

New to Foundry? Read [foundry-concepts.md](foundry-concepts.md) first. It explains
resources, deployments, quota, cost and access, and whether to use a shared or a
dedicated resource.

**Quick path: `scripts/setup-foundry.sh`** does Steps 1, 2 and 4a in one go. It creates
the resource group, the Foundry resource and the model deployment, gives you the
data-plane role, and prints the `.env` values. It is safe to re-run: existing parts
are skipped, and a missing resource group is created. Networking (Step 5) stays
manual. A suggested layout is one resource for non-prod clusters and one for prod
([layout](foundry-concepts.md#layout-one-resource-for-non-prod-one-for-prod)):

```bash
# ./scripts/setup-foundry.sh <subscription> <resource-group> <foundry-name> <location>
./scripts/setup-foundry.sh "<subscription>" my-foundry-rg my-foundry westeurope
# optional: [DEPLOYMENT_NAME] [MODEL_NAME] [MODEL_VERSION] [SKU_NAME] [CAPACITY] [UPGRADE_OPTION]
#           defaults: k8s-servant gpt-5.4-mini 2026-03-17 DataZoneStandard 50 OnceCurrentVersionExpired
```

The steps below explain what the script does and how to do each part by hand.

**Using an existing shared resource** (one your organisation already runs):
skip Step 1, create the agent's own deployment in Step 2 (e.g. `k8s-servant`), and
continue from Step 3. Agree any network change (Step 5) with the resource's owners.

Facts and commands were checked against Microsoft Learn and `az` 2.90 on 2026-10-09.
Links to the sources are at the end of each section and in [Reference](#12-reference).

## Contents

1. [What you are setting up and why](#1-what-you-are-setting-up-and-why)
2. [Prerequisites](#2-prerequisites)
3. [Step 1 – Create the Foundry resource](#3-step-1-create-the-foundry-resource)
4. [Step 2 – Deploy a chat model](#4-step-2-deploy-a-chat-model)
5. [Step 3 – Find the endpoint and deployment name](#5-step-3-find-the-endpoint-and-deployment-name)
6. [Step 4 – Give access](#6-step-4-give-access)
7. [Step 5 – Networking](#7-step-5-networking)
8. [Step 6 – Smoke test](#8-step-6-smoke-test)
9. [Cost and limits](#9-cost-and-limits)
10. [Content safety and data handling](#10-content-safety-and-data-handling)
11. [Troubleshooting](#11-troubleshooting)
12. [Reference](#12-reference)

## 1. What you are setting up and why

```
 k8s-servant ──HTTPS + Entra ID token──▶ Foundry resource ──▶ model deployment "gpt-5-4-mini"
 (laptop: az login,                    https://<name>.openai.azure.com/openai/v1/
  cluster: Workload Identity)           (OpenAI-compatible "v1" API)
```

| Term | Meaning |
|---|---|
| **Foundry resource** | An Azure resource of kind `AIServices`. It owns the endpoint (URL), keys, network rules and model deployments. You need exactly one. |
| **Project** | A workspace inside the resource for the Foundry portal's agents, evaluations and so on. **The agent does not need one**: deployments live on the resource and the agent calls the resource's v1 endpoint. Creating one does no harm, and the portal may create one for you. |
| **Deployment** | A named instance of a model on the resource, with its own capacity (quota). The agent calls a **deployment name**, not a model name. |
| **Endpoint** | The resource URL. The agent uses its OpenAI-compatible **v1 API** at `https://<name>.openai.azure.com/openai/v1/`, which needs no `api-version` parameter. |

**Why this API.** For GPT and other OpenAI-style models the agent uses the standard
`openai` Python SDK with the Responses API (`LLM_PROVIDER=foundry`), which, unlike Chat
Completions, can carry a reasoning model's hidden steps between tool calls
([model-providers.md](model-providers.md#why-three-apis)). Claude on Foundry uses the
Messages API and partner models with only Chat Completions use `openai-compatible`
(recipes in the same page). Changing the model within a provider later
only changes `LLM_MODEL`.

**Why Entra ID.** Locally the agent uses your `az login` session; in the cluster it uses
Workload Identity. This avoids long-lived API keys that would have to be stored and
rotated.

## 2. Prerequisites

- **Azure CLI** 2.60 or newer (`az version`), logged in with `az login`, on the right
  subscription (`az account set --subscription "<name or id>"`).
- **Permissions on the target resource group:**
  - *Contributor*, to create the resource and deployments;
  - *User Access Administrator* or *Owner*, to assign roles in Step 4.
- **A region with the model you want.**
  - For EU data residency choose an EU region, for example **Sweden Central** or **West
    Europe**. gpt-5.4-mini is offered there as GlobalStandard and DataZoneStandard.
  - Check before you start, using the commands in Step 2.
- **Quota.** New subscriptions have a default tokens-per-minute (TPM) quota per model and
  region. Raising it is a request in the portal (Foundry → Quota).

Set variables used in the commands below:

```bash
RG=my-foundry-rg              # resource group (create it with: az group create -n $RG -l $LOCATION)
LOCATION=westeurope           # or swedencentral
FOUNDRY=my-foundry            # resource name; also used as the custom subdomain, must be globally unique
DEPLOYMENT=gpt-5-4-mini        # name you give the deployment; the agent uses it as LLM_MODEL
```

## 3. Step 1: Create the Foundry resource

**Portal:** [ai.azure.com](https://ai.azure.com), then **Create new** → **Foundry
resource**. Pick the subscription, resource group, region and name. Alternatively, in the
Azure portal go to **Create a resource** → **Foundry**.

**CLI:**

```bash
az cognitiveservices account create \
  --name "$FOUNDRY" \
  --resource-group "$RG" \
  --location "$LOCATION" \
  --kind AIServices \
  --sku S0 \
  --custom-domain "$FOUNDRY" \
  --assign-identity \
  --yes
```

| Flag | Why |
|---|---|
| `--kind AIServices` | The Foundry resource type (multi-service AI resource that hosts model deployments). |
| `--sku S0` | Standard pricing tier; you pay per token used. |
| `--custom-domain` | Gives the resource its own hostnames (`<name>.openai.azure.com`, `<name>.services.ai.azure.com`, `<name>.cognitiveservices.azure.com`). **Required** for Entra ID authentication and private endpoints. Must be globally unique (error `CustomDomainInUse` otherwise). |
| `--assign-identity` | System-assigned identity for the resource. It is needed if you ever create a project on this resource; adding it later is harder. |
| `--yes` | Accepts the responsible-AI terms non-interactively. |

Optional: a project, if you want to use the Foundry portal's playground with a
project scope.

```bash
az cognitiveservices account project create --name "$FOUNDRY" --resource-group "$RG" \
  --project-name k8s-servant --location "$LOCATION"
```

Sources: [Quickstart: create Foundry resources](https://learn.microsoft.com/azure/foundry/tutorials/quickstart-create-foundry-resources),
[Create projects](https://learn.microsoft.com/azure/foundry/how-to/create-projects).

## 4. Step 2: Deploy a chat model

### Choose a model

The agent needs a chat model that is good at **tool calling** (function calling) and
has a **large context window**: tool outputs such as pod lists and logs are big.

| Model | Context window | Why you would pick it |
|---|---|---|
| **gpt-5.4-mini** (2026-03-17) – *recommended start* | 400k (272k in / 128k out) | Good tool calling, low cost, fast. Retires 2027-09-21. |
| gpt-5.4 | ~1M | Deeper reasoning on complex incidents; higher cost. |
| gpt-5.5 / gpt-5.6 variants | see Learn | Newest generation (gpt-5.6 retires 2028-01-11). |
| ~~gpt-4.1~~ | – | **Deprecated.** A subscription that never deployed it cannot create new deployments. It retires 2027-04-14. |

Check what your resource can deploy, and which versions exist:

```bash
az cognitiveservices account list-models --name "$FOUNDRY" --resource-group "$RG" \
  --query "[?contains(name,'gpt-5')].{name:name, version:version, skus:skus[].name}" -o table
```

### Choose the deployment type (where prompts are processed)

| `--sku-name` | Where data is processed | Notes |
|---|---|---|
| `GlobalStandard` | Any Azure region worldwide | Microsoft's default. Highest quota, and new models arrive here first. |
| `DataZoneStandard` | Within the **EU data zone** (EU Data Boundary, may include EFTA countries) when the resource is in an EU region | **Recommended when data must stay in the EU**: keeps cluster data such as object names and log lines inside the EU. |
| `Standard` | Only in the resource's region | Lower quota; newest models arrive here last. |

Data **at rest** stays in the resource's geography for every type. Only **processing**
differs.

### Choose the capacity

`--sku-capacity` is in **units of 1,000 tokens per minute** (TPM). Rough sizing:

- One `ask` uses about 5–15 model calls. Each call resends the system prompt and the
  conversation so far, about 10k–40k tokens at the end of a long investigation, so one
  question totals about **100k–300k tokens over 1–3 minutes**.
- An hourly health check is similar. **Measured on a test cluster (~600 Deployments, ~1,250 pods):**
  150k–225k tokens per check, about 10 model calls in 2–3 minutes, with peaks of
  about 90k tokens in one minute. The log line `model call: N prompt + M completion
  tokens` shows the usage of every call.
- With 50 (= 50k TPM) deep checks hit HTTP 429 (the SDK retries, which slows a
  check down and can still fail). **Use 150–200** if the subscription's quota allows
  it (Foundry portal → **Quota**). Capacity only limits throughput; you pay for tokens
  used, not for capacity. Change an existing deployment in the Foundry portal:
  **Models + endpoints** → the deployment → **Edit** → **Tokens per Minute Rate Limit**.
- **Reasoning effort.** gpt-5.x models reason only when the request asks for it. The
  agent sends `reasoning_effort` = `LLM_REASONING_EFFORT` (default `medium`).
  Without it gpt-5.4-mini answered after 3 shallow tool calls and missed firing alerts.

### Deploy

**Portal:** deployments are managed in the **Foundry portal**, not the Azure portal; the Azure
portal's "Model deployments" entry only redirects there. Open [ai.azure.com](https://ai.azure.com) →
select the Foundry resource → **Models + endpoints** → **Deploy model** → **Deploy base
model** → pick the model, deployment type and capacity, and set the deployment name.

**CLI:**

```bash
az cognitiveservices account deployment create \
  --name "$FOUNDRY" \
  --resource-group "$RG" \
  --deployment-name "$DEPLOYMENT" \
  --model-name gpt-5.4-mini \
  --model-version 2026-03-17 \
  --model-format OpenAI \
  --sku-name DataZoneStandard \
  --sku-capacity 50
```

A new deployment can take a few minutes before requests to it succeed. Until then you
get HTTP 404 `DeploymentNotFound`.

**Version upgrade policy.** A deployment pins a model version, but its upgrade policy
decides what happens when Microsoft releases a newer one:

| Policy (portal name) | What happens | For the agent |
|---|---|---|
| `OnceNewDefaultVersionAvailable` ("Once a new default version is available") | Moves to the new default version within two weeks of it becoming default. **Default in the portal and the CLI.** | No: the model changes under a tested agent without anyone deciding it |
| `OnceCurrentVersionExpired` ("Upgrade when expired") | Keeps the version until its retirement date (shown on the deployment page), then moves to the default version | **Yes** (the script's default) |
| `NoAutoUpgrade` | Keeps the version; the deployment **stops working** at retirement | Only if a forced upgrade is worse than an outage |

With `OnceCurrentVersionExpired`, upgrading is your decision: deploy the new version on
non-prod, test, then change prod (see
[foundry-concepts.md](foundry-concepts.md)). Before the retirement date, plan the move.

The CLI cannot set the policy (`az cognitiveservices account deployment` has no option
for it), so the script sets it on the ARM resource, also on an existing deployment:

```bash
az resource update \
  --ids "$(az cognitiveservices account show -n "$FOUNDRY" -g "$RG" --query id -o tsv)/deployments/$DEPLOYMENT" \
  --set properties.versionUpgradeOption=OnceCurrentVersionExpired \
  --api-version 2025-06-01
```

**Portal:** Foundry portal → **Models + endpoints** → the deployment → **Edit** →
**Version update policy**. Check the result with
`az cognitiveservices account deployment show -n "$FOUNDRY" -g "$RG" --deployment-name "$DEPLOYMENT" --query properties.versionUpgradeOption`.

Sources: [Deploy models](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/create-model-deployments),
[Deployment types](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/deployment-types),
[Quota](https://learn.microsoft.com/azure/foundry/openai/how-to/quota),
[Models](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure),
[Retirements](https://learn.microsoft.com/azure/foundry/openai/concepts/model-retirements),
[Upgrade policy](https://learn.microsoft.com/azure/foundry/openai/how-to/working-with-models#model-deployment-upgrade-configuration).

## 5. Step 3: Find the endpoint and deployment name

**Portal:** Azure portal → the Foundry resource → **Overview** (or **Resource Management** →
**Keys and Endpoint**). The Foundry portal shows the same values on the resource's overview page.

**CLI:**

```bash
az cognitiveservices account show --name "$FOUNDRY" --resource-group "$RG" --query properties.endpoint -o tsv
az cognitiveservices account deployment list --name "$FOUNDRY" --resource-group "$RG" --query "[].name" -o tsv
```

Set the agent's variables (in `.env` locally, or Helm values in the cluster):

```bash
LLM_PROVIDER=foundry
LLM_ENDPOINT=https://<name>.openai.azure.com/
LLM_MODEL=gpt-5-4-mini
LLM_AUTH=entra
```

- **Use the `<name>.openai.azure.com` hostname.** The v1 API is documented for
  `https://<name>.openai.azure.com/openai/v1/` and
  `https://<name>.services.ai.azure.com/openai/v1/`. If `properties.endpoint` prints a
  `*.cognitiveservices.azure.com` URL, build the `openai.azure.com` one from your
  custom subdomain.
- **You do not need to add `/openai/v1/`.** The agent appends it (see
  `normalize_endpoint` in `k8s_servant/llm/providers.py`). The endpoint must start with `https://`.
- `LLM_MODEL` is the **deployment name** from Step 2, not the model name.

## 6. Step 4: Give access

The agent authenticates with **Entra ID**. Whoever calls the model needs a
**data-plane role** on the resource. *Owner* and *Contributor* are not enough: they
manage the resource but cannot call models.

**Which role.** It depends on the model ([Microsoft Learn](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/configure-entra-id#troubleshooting)):
- **GPT and other OpenAI models:** **Cognitive Services OpenAI User** on the Foundry
  resource. It is the least-privileged role that can call them, and it grants access to
  OpenAI models only.
- **Claude and other non-OpenAI models** (DeepSeek, Llama, Grok, partner models):
  **Cognitive Services User**. *Cognitive Services OpenAI User* is not enough for them.
  `scripts/setup-workload-identity.sh` assigns it when run with
  `MODEL_ROLE="Cognitive Services User"`.
- *Foundry User* (formerly *Azure AI User*) also works for both but is broader, granting
  all Foundry data actions.

Role assignments take **up to about 5 minutes** to apply.

```bash
FOUNDRY_ID=$(az cognitiveservices account show --name "$FOUNDRY" --resource-group "$RG" --query id -o tsv)
```

**Portal:** role assignments are made in the **Azure portal**: the Foundry resource → **Access
control (IAM)** → **Add** → **Add role assignment** → role *Cognitive Services OpenAI User* (or *Cognitive
Services User* for non-OpenAI models) → select the user or managed identity → **Review + assign**. As Owner of the resource you can
assign it to yourself.

### a) You, for local development

```bash
az role assignment create \
  --assignee "$(az ad signed-in-user show --query id -o tsv)" \
  --role "Cognitive Services OpenAI User" \
  --scope "$FOUNDRY_ID"
az login   # if not logged in; the agent picks up this session through DefaultAzureCredential
```

### b) The agent in the cluster

The agent pod authenticates with AKS **Workload Identity**: a user-assigned managed
identity with a federated credential for the agent's ServiceAccount.
`scripts/setup-workload-identity.sh` creates the identity and assigns it this
role (set `MODEL_ROLE="Cognitive Services User"` for Claude and other non-OpenAI models;
[deployment.md](deployment.md#1-workload-identity-aks)). Done by hand, the assignment is:

```bash
az role assignment create \
  --assignee-object-id "<managed identity principalId>" \
  --assignee-principal-type ServicePrincipal \
  --role "Cognitive Services OpenAI User" \
  --scope "$FOUNDRY_ID"
```

### c) API key: local experiments only

```bash
az cognitiveservices account keys list --name "$FOUNDRY" --resource-group "$RG"
# then: LLM_AUTH=api-key and put <key1> in the file LLM_API_KEY_FILE points to
```

Keys are discouraged:
- they are long-lived shared secrets;
- anyone holding one can use the model;
- they need rotation.

**Recommended:** use `LLM_AUTH=entra` (the agent then refuses to start if a key is
also configured) and set `disableLocalAuth: true` on the resource, so keys cannot be
used at all.

Reading keys needs *Cognitive Services Contributor*; the OpenAI User role cannot see
them. Once everyone uses Entra ID, **disable key authentication** altogether:

```bash
az resource update --ids "$FOUNDRY_ID" --set properties.disableLocalAuth=true
```

Microsoft documents this setting through PowerShell, Bicep/ARM (`disableLocalAuth: true`)
and Azure Policy. The `az resource update` form above sets the same ARM property. It can
take a while to apply; afterwards an old key gets HTTP 401.

**Cluster in another tenant than Foundry.** If the pod's identity has no role on the
Foundry resource (for example because the resource lives in another tenant or
subscription that you do not control), use a Foundry **API key** kept in Key Vault:
Workload Identity still reads the key from Key Vault, and `LLM_AUTH: api-key` uses it.
Deliver the key as a Secret or mounted file as in
[model-providers.md](providers/openai.md) (`llmApiKey.existingSecret` or
`llmApiKey.file`). In that case do not disable local auth on that resource.

**Token scope.** The agent requests tokens for `https://ai.azure.com/.default`, the
current scope in Microsoft's documentation. It can be changed with `LLM_TOKEN_SCOPE`.

Sources: [Configure Entra ID](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/configure-entra-id),
[Foundry RBAC](https://learn.microsoft.com/azure/foundry/concepts/rbac-foundry),
[Disable local auth](https://learn.microsoft.com/azure/ai-services/disable-local-auth).

## 7. Step 5: Networking

**From a laptop.** If the resource allows public network access (the default), nothing
else is needed.

**From the cluster.** If the cluster has open outbound internet access, nothing else is
needed. If it is **private** or its egress goes through a **firewall**, the agent pod
can only reach the Foundry resource if one of these is in place:

| Option | What to do | Pros / cons |
|---|---|---|
| **A. Private endpoint (recommended)** | Create a private endpoint for the resource in a VNet the cluster can route to, and register it in private DNS (see below). Optionally disable public network access afterwards. | Traffic never leaves the private network, and the resource can be closed to the internet. Needs DNS integration with the hub. |
| **B. Firewall rule** | Allow HTTPS (443) from the cluster's egress to `<name>.openai.azure.com`. | Quick, but the resource stays publicly reachable. |

**Option A, the commands.**

```bash
az network private-endpoint create \
  --name "pe-$FOUNDRY" \
  --resource-group "<network rg>" \
  --vnet-name "<vnet>" --subnet "<subnet>" \
  --private-connection-resource-id "$FOUNDRY_ID" \
  --group-id account \
  --connection-name "pe-$FOUNDRY"
```

- **Private DNS zones**, one per hostname you use, each linked to the VNets whose DNS
  the cluster uses (the hub DNS):
  - `privatelink.openai.azure.com`: **required**, because the agent uses
    `<name>.openai.azure.com`.
  - `privatelink.services.ai.azure.com` and `privatelink.cognitiveservices.azure.com`:
    add them if you use those hostnames, for example from the portal.
- **Check the result.** From inside the cluster, `<name>.openai.azure.com` must
  resolve to the private IP of the endpoint.
- **Close public access** once private access works: in the portal, **Networking** →
  **Public network access: Disabled**.
- **Ownership.** Private DNS zones and firewall rules often belong to a network team.
  Request them with the resource ID and the hostname.

**Testing from inside the cluster.** Once the agent is deployed ([deployment.md](deployment.md)), run
`kubectl -n k8s-servant exec deploy/k8s-servant -c agent -- k8s-servant doctor`.
Its `model` line proves DNS, network, authentication and deployment in one go.

Sources: [Private link for Foundry](https://learn.microsoft.com/azure/foundry/how-to/configure-private-link),
[Private endpoint DNS](https://learn.microsoft.com/azure/private-link/private-endpoint-dns),
[Virtual networks](https://learn.microsoft.com/azure/ai-services/cognitive-services-virtual-networks).

## 8. Step 6: Smoke test

### With curl (independent of the agent)

```bash
TOKEN=$(az account get-access-token --scope https://ai.azure.com/.default --query accessToken -o tsv)
curl -sS -X POST "https://$FOUNDRY.openai.azure.com/openai/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $TOKEN" \
  -d "{\"model\": \"$DEPLOYMENT\", \"messages\": [{\"role\": \"user\", \"content\": \"Reply with OK\"}]}"
```

- `"model"` is the **deployment name**.
- With a key, replace the `Authorization` header with `-H "api-key: $KEY"`.

### With the agent

```console
$ set -a; source .env; set +a
$ k8s-servant doctor
[OK  ] config           endpoint https://my-foundry.openai.azure.com/, deployment gpt-5-4-mini
...
[OK  ] model            answered 'OK' in 1.4s
```

The `model` check sends one tiny request. If it fails, the line says why; see
[Troubleshooting](#11-troubleshooting).

## 9. Cost and limits

- **Billing.** You pay **per token**, with input and output priced separately (per 1M
  tokens; prices on the
  [Azure OpenAI pricing page](https://azure.microsoft.com/pricing/details/azure-openai/)).
  For reasoning models, internal reasoning tokens are billed as output.
- **Capacity (TPM)** limits throughput only. An idle deployment costs nothing.
- **Typical usage.** One `ask` is about 100k–300k tokens. An hourly health check
  is about 24 such runs a day (fewer when unchanged checks are skipped). Multiply by the per-token price of your model
  for a daily estimate; mini models keep this small.
- **Budget alert.** Azure portal → **Cost Management** → **Budgets** → create one on the
  resource group, filtered to the Foundry resource, with an email alert at for example
  80%. A budget alerts but does not stop spending.

Sources: [Manage costs](https://learn.microsoft.com/azure/foundry/concepts/manage-costs).

## 10. Content safety and data handling

- **Content filter.** Every deployment has a default guardrail:
  - hate, sexual, violence and self-harm are filtered at *Medium* on prompts and
    completions;
  - Prompt Shields (jailbreak) and protected-material checks are on.

  A filtered request returns HTTP 400 with `content_filter`. Normal cluster data does
  not trigger it.
- **Your data.**
  - Prompts and completions are **not used to train models** and are not shared with
    OpenAI. The models keep no state between requests.
  - Abuse monitoring may store flagged content for review in the resource's geography
    (for an EU resource, reviewers are in the EEA).
- **What the agent sends.** The system prompt (rules, environment description, skill
  catalog) and the outputs of its tools: object names, statuses, events and log lines.
  **Secrets are never sent**:
  - the kubectl tool refuses them;
  - the MCP server is configured to deny them;
  - in the cluster, RBAC forbids them;
  - the MCP `config` toolset, which would expose the kubeconfig, is disabled.

  Remember that application **logs** can contain sensitive data if applications log it.

Sources: [Default safety policies](https://learn.microsoft.com/azure/foundry/guardrails/default-safety-policies),
[Data privacy](https://learn.microsoft.com/azure/foundry/responsible-ai/openai/data-privacy),
[Abuse monitoring](https://learn.microsoft.com/azure/foundry/openai/concepts/abuse-monitoring).

## 11. Troubleshooting

`k8s-servant` turns these into one-line advice (`model_error_text` in `k8s_servant/llm/errors.py`).

| Symptom | Likely cause | Fix |
|---|---|---|
| **401** "Access token is missing, invalid, audience is incorrect" | Not logged in, expired session, wrong token scope, or a wrong or rotated API key | `az login`; check `LLM_TOKEN_SCOPE` (default `https://ai.azure.com/.default`); check the key in `LLM_API_KEY_FILE` |
| **401** "PermissionDenied … lacks the required data action" | The identity has no data-plane role, or the wrong one for the model | Assign *Cognitive Services OpenAI User* (OpenAI models) or *Cognitive Services User* (Claude and other non-OpenAI models) on the resource (Step 4) and wait ~5 minutes |
| **403** | Network rules: public access disabled or IP not allowed, or the resource is suspended | Use the private endpoint and check that the hostname resolves to its private IP (Step 5) |
| **404** `DeploymentNotFound` | Wrong `LLM_MODEL` (model name used instead of deployment name), wrong resource in `LLM_ENDPOINT`, or the deployment is less than ~5 minutes old | Compare with `az cognitiveservices account deployment list`; wait a few minutes |
| **410** | The model version has been retired | Deploy a current model (Step 2) |
| **429** | Tokens-per-minute or requests-per-minute exceeded | The SDK retries automatically (3×). If it persists, raise `--sku-capacity` |
| "cannot reach LLM_ENDPOINT" / timeout | DNS, firewall or private endpoint problem; on a laptop, VPN or proxy | Step 5; `nslookup <name>.openai.azure.com`; try the curl test |
| "could not get an Entra ID token" (`CredentialUnavailableError`) | No credential found: not logged in locally, or Workload Identity not set up in the pod | `az login`; in the cluster check the ServiceAccount annotation and the federated credential ([deployment.md](deployment.md#1-workload-identity-aks)) |
| `LLM_ENDPOINT must start with https://` | Endpoint typo | Use the value from Step 3 |

Source: [Troubleshoot errors](https://learn.microsoft.com/azure/foundry/openai/how-to/troubleshoot-errors).

## 12. Reference

- [Quickstart: create Foundry resources](https://learn.microsoft.com/azure/foundry/tutorials/quickstart-create-foundry-resources)
- [v1 API and API version lifecycle](https://learn.microsoft.com/azure/foundry/openai/api-version-lifecycle)
- [Deploy models](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/create-model-deployments) · [Deployment types](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/deployment-types) · [Quota](https://learn.microsoft.com/azure/foundry/openai/how-to/quota)
- [Models sold by Azure](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure) · [Region availability](https://learn.microsoft.com/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability) · [Retirement schedule](https://learn.microsoft.com/azure/foundry/openai/concepts/model-retirement-schedule)
- [Configure Entra ID](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/configure-entra-id) · [Foundry RBAC](https://learn.microsoft.com/azure/foundry/concepts/rbac-foundry) · [Disable local auth](https://learn.microsoft.com/azure/ai-services/disable-local-auth)
- [Private link](https://learn.microsoft.com/azure/foundry/how-to/configure-private-link) · [Private endpoint DNS](https://learn.microsoft.com/azure/private-link/private-endpoint-dns)
- [Default safety policies](https://learn.microsoft.com/azure/foundry/guardrails/default-safety-policies) · [Data privacy](https://learn.microsoft.com/azure/foundry/responsible-ai/openai/data-privacy)
- [Manage costs](https://learn.microsoft.com/azure/foundry/concepts/manage-costs) · [Troubleshoot errors](https://learn.microsoft.com/azure/foundry/openai/how-to/troubleshoot-errors)
