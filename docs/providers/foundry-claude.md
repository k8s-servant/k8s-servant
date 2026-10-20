# Claude on Azure AI Foundry

**Choose it when** you want Claude but inside Azure: same Entra ID sign-in and billing as
the [Foundry recipe](foundry.md), with Claude's Messages API. Deploy a Claude model in your Foundry
resource first ([foundry-setup.md](../foundry-setup.md), Step 2, choose a Claude model).

Laptop `.env`:

```bash
LLM_PROVIDER=anthropic
LLM_ENDPOINT=https://my-foundry.services.ai.azure.com   # /anthropic is added for you
LLM_MODEL=claude-sonnet-5-5                              # the deployment name
LLM_AUTH=entra
```

Cluster: [`examples/values-azure-claude.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-azure-claude.yaml).

```yaml
azureWorkloadIdentity:
  enabled: true
  clientId: "00000000-0000-0000-0000-000000000000"
  tenantId: "00000000-0000-0000-0000-000000000000"
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: anthropic
  LLM_ENDPOINT: https://my-foundry.services.ai.azure.com
  LLM_MODEL: claude-sonnet-5-5
  LLM_AUTH: entra
```

Entra ID setup is the same as for [Foundry](foundry.md),
except for the role: *Cognitive Services OpenAI User* covers only OpenAI models, and
Claude needs *Cognitive Services User* ([Microsoft Learn](https://learn.microsoft.com/azure/foundry/foundry-models/how-to/configure-entra-id#troubleshooting)). Run
`MODEL_ROLE="Cognitive Services User" scripts/setup-workload-identity.sh ...`, then put the
identity's client and tenant IDs in `azureWorkloadIdentity`. Entra ID is accepted for `anthropic` only because the endpoint
is a Foundry address.

Expected `doctor` model line:

```
model: anthropic / claude-sonnet-5-5 via Messages API, Entra ID (Workload Identity)
```
