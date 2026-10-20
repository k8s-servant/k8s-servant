# GPT and other models on Microsoft Foundry

**Choose it when** your organisation runs on Azure and you want a model inside your own
tenant, in an EU or US data zone, with no key to manage: on AKS the pod signs in with
Workload Identity. It serves GPT models and others (for example DeepSeek, Llama, Grok)
through the Responses API. *Cognitive Services OpenAI User*, the role the setup script
assigns by default, covers only OpenAI models: for DeepSeek, Llama, Grok and other
non-OpenAI models run the script with `MODEL_ROLE="Cognitive Services User"`
([Microsoft Learn](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/configure-entra-id#troubleshooting)). Partner models that offer only Chat Completions use
[`openai-compatible`](../openai-compatible.md) with a Foundry endpoint,
and Claude has [its own recipe](claude.md).

The Azure side (resource, deployment, roles, network) is in
[Foundry setup guide](setup.md) and the background in
[Foundry concepts](concepts.md); `scripts/setup-foundry.sh` does it in one go.

Laptop `.env` (sign in with `az login`; no key; needs the `azure` extra):

```bash
LLM_PROVIDER=foundry
LLM_ENDPOINT=https://my-foundry.openai.azure.com/   # /openai/v1/ is added for you
LLM_MODEL=my-deployment                              # the deployment name
LLM_AUTH=entra
```

Cluster: [`examples/values-azure.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-azure.yaml) (Workload Identity,
Teams webhook synced from Key Vault by External Secrets) or
[`examples/values-azure-csi.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-azure-csi.yaml) (no Kubernetes
Secrets at all). The relevant part:

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

Entra ID setup: run `scripts/setup-workload-identity.sh` (identity, federated credential,
the *Cognitive Services OpenAI User* role on the Foundry resource), put the client and
tenant IDs it prints into `azureWorkloadIdentity`, then follow
[Foundry setup guide](setup.md). With `LLM_AUTH=entra` no key is ever needed, and you
can set `disableLocalAuth: true` on the resource so keys cannot be used at all.

If you must use a key instead (a quick experiment, or another tenant), create the Secret
as in [OpenAI](../openai.md), set `LLM_AUTH: api-key` and `llmApiKey.existingSecret`.

Expected `doctor` model line:

```
model: foundry / my-deployment via Responses API, Entra ID (Workload Identity)
```

On a laptop the source reads `Entra ID (az login / DefaultAzureCredential)`.
