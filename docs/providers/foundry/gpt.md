# GPT and other models on Microsoft Foundry

**Choose it when** your organisation runs on Azure and you want a model inside your own
tenant, in an EU or US data zone. On AKS the pod signs in with Workload Identity, with
no key to manage (recommended); on any cluster it can use an API key instead. It serves
GPT models and others (for example DeepSeek, Llama, Grok) through the Responses API.

*Cognitive Services OpenAI User*, the role the setup script assigns by default, covers
only OpenAI models: for DeepSeek, Llama, Grok and other non-OpenAI models run the
script with `MODEL_ROLE="Cognitive Services User"`
([Microsoft Learn](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/configure-entra-id#troubleshooting)).
Partner models that offer only Chat Completions use
[`openai-compatible`](../openai-compatible.md) with a Foundry endpoint, and Claude has
[its own recipe](claude.md).

The Azure side (resource, deployment, roles, network) is in
[Foundry setup guide](setup.md) and the background in
[Foundry concepts](concepts.md); `scripts/setup-foundry.sh` does it in one go.

## How the agent signs in

| | Workload Identity (recommended) | API key |
|---|---|---|
| Where | AKS (on a laptop: `az login`) | Any cluster: AKS, EKS, GKE, on-premises |
| Credential to store | None: the pod gets short-lived Entra ID tokens | A long-lived key, in a Secret you create |
| If it leaks | Nothing to leak | Anyone with the key can call the model until you rotate it |
| Setup | `scripts/setup-workload-identity.sh` | Copy the key into a Secret |

**Workload Identity is the recommended, more secure choice:** there is no key to store,
rotate or leak, access is one role on one resource, and you can switch keys off on the
resource entirely (`disableLocalAuth: true`). Use a key for a quick try, or when the
cluster is not AKS.

## On a laptop

`.env` with Entra ID (sign in with `az login`; needs the `azure` extra):

```bash
LLM_PROVIDER=foundry
LLM_ENDPOINT=https://my-foundry.openai.azure.com/   # /openai/v1/ is added for you
LLM_MODEL=my-deployment                              # the deployment name
LLM_AUTH=entra
```

With a key instead, write it to a file only you can read (the command below prints
nothing, so the key stays out of your terminal and shell history) and point the agent
at it:

```bash
mkdir -p ~/.config/k8s-servant && chmod 700 ~/.config/k8s-servant
az cognitiveservices account keys list --name my-foundry --resource-group my-foundry-rg \
  --query key1 -o tsv > ~/.config/k8s-servant/llm-api-key
chmod 600 ~/.config/k8s-servant/llm-api-key
```

```bash
LLM_AUTH=api-key
LLM_API_KEY_FILE=~/.config/k8s-servant/llm-api-key   # ~ expands with: set -a; source .env; set +a
```

## In the cluster with Workload Identity (recommended)

Values: [`examples/values-azure.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-azure.yaml)
(Teams webhook synced from Key Vault by External Secrets) or
[`examples/values-azure-csi.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-azure-csi.yaml)
(no Kubernetes Secrets at all). The relevant part:

```yaml
azureWorkloadIdentity:
  enabled: true
  clientId: "00000000-0000-0000-0000-000000000000"
  tenantId: "00000000-0000-0000-0000-000000000000"
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: foundry
  LLM_ENDPOINT: https://my-foundry.openai.azure.com/openai/v1/
  LLM_MODEL: my-deployment
  LLM_AUTH: entra
```

Run `scripts/setup-workload-identity.sh` (identity, federated credential, the
*Cognitive Services OpenAI User* role on the Foundry resource) and put the client and
tenant IDs it prints into `azureWorkloadIdentity`. The full AKS walk-through is in
[Deploying to a cluster](../../deployment.md#aks). With `LLM_AUTH=entra` no key is
ever used.

Expected `doctor` model line:

```
model: foundry / my-deployment via Responses API, Entra ID (Workload Identity)
```

On a laptop the source reads `Entra ID (az login / DefaultAzureCredential)`.

## In the cluster with an API key

Values: [`examples/values-foundry-key.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-foundry-key.yaml).
The resource must allow key access (`disableLocalAuth: false`). Create the Secret from
the key file, without printing the key:

```bash
kubectl create namespace k8s-servant
az cognitiveservices account keys list --name my-foundry --resource-group my-foundry-rg \
  --query key1 -o tsv > api-key
kubectl -n k8s-servant create secret generic k8s-servant-llm --from-file=./api-key
rm api-key
```

The relevant part of the values:

```yaml
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: foundry
  LLM_ENDPOINT: https://my-foundry.openai.azure.com/
  LLM_MODEL: my-deployment
  LLM_AUTH: api-key
llmApiKey:
  existingSecret: k8s-servant-llm
```

`LLM_AUTH: api-key` refuses to start without the key, so a missing Secret shows up at
once instead of as a silent fall-back to Entra ID. To rotate the key, regenerate it in
Azure, update the Secret and restart the pod
(`kubectl -n k8s-servant rollout restart deploy/k8s-servant`).
