# Deploying to a cluster

How k8s-servant gets into a Kubernetes cluster and how to run it there: prerequisites,
the model identity, values, installing with Helm or Flux, checks after the first
deployment, and day-2 operations. The examples use AKS with Azure AI Foundry, Workload
Identity and Azure Key Vault, because that is the tested path; the chart itself works
on any cluster.

## Table of Contents

- [What gets deployed](#what-gets-deployed)
- [Published artifacts](#published-artifacts)
- [Prerequisites](#prerequisites)
- [1. Workload Identity (AKS)](#1-workload-identity-aks)
- [2. Network](#2-network)
- [3. Values for the cluster](#3-values-for-the-cluster)
- [4. Install with Helm](#4-install-with-helm)
- [5. Install with Flux](#5-install-with-flux)
- [6. Check the deployment](#6-check-the-deployment)
- [When a cluster is recreated](#when-a-cluster-is-recreated)
- [Day-2 operations](#day-2-operations)

---

## What gets deployed

```
namespace k8s-servant
├── Deployment k8s-servant           1 replica, strategy Recreate
│   ├── container agent                 image ghcr.io/k8s-servant/k8s-servant:<version>
│   │                                   `k8s-servant serve`: hourly checks, morning report
│   └── container mcp                   image ghcr.io/containers/kubernetes-mcp-server:<pinned tag>
│                                       read-only, Secrets denied, listens on 127.0.0.1:8080
├── ServiceAccount k8s-servant       optionally federated with a managed identity (Workload Identity)
├── ConfigMap k8s-servant-config     settings (envFrom), from appConfig
├── ConfigMap k8s-servant-knowledge  environment.md, cluster.md, skill__*.md → /knowledge
├── ConfigMap k8s-servant-mcp        the MCP server's config.toml
├── PersistentVolumeClaim k8s-servant-data   1 Gi, /data: memory.db + notes.md (kept on uninstall)
├── SecretStore k8s-servant-keyvault          (optional) External Secrets → Key Vault, with the agent's identity
├── ExternalSecret k8s-servant-teams-webhook  (optional) → Secret k8s-servant-teams-webhook (file /var/run/secrets/teams/webhook-url)
cluster-wide
├── ClusterRoleBinding k8s-servant-view            → built-in "view" (namespaced, no Secrets)
└── ClusterRoleBinding k8s-servant-cluster-reader  → ClusterRole k8s-servant-cluster-reader
```

Security properties (non-root, read-only root filesystem, RBAC without Secrets, webhook
URL only as a file) are tested in `tests/test_helm_chart.py`. Details:
[security.md](security.md#in-the-cluster).

## Published artifacts

| Artifact | Location | Notes |
|---|---|---|
| Agent image | `ghcr.io/k8s-servant/k8s-servant:<version>` | Built by GitHub Actions, signed with cosign (keyless), SBOM and provenance attached |
| MCP sidecar image | `ghcr.io/containers/kubernetes-mcp-server` | The upstream image; the chart pins a tested tag (`mcp.image.tag`) |
| Helm chart | `oci://ghcr.io/k8s-servant/charts/k8s-servant` | Source in `charts/k8s-servant/`; chart `version` = `appVersion` = image version |

Releases are cut by release-please from Conventional Commit PR titles; every release
publishes the image and the chart with the same version.

To verify the image signature before you deploy:

```bash
cosign verify ghcr.io/k8s-servant/k8s-servant:<version> \
  --certificate-identity "https://github.com/k8s-servant/k8s-servant/.github/workflows/release-please.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

[verifying-releases.md](verifying-releases.md) explains how the signing works, how to
verify the chart, and how to enforce signatures with an admission policy.

If your cluster only admits images from your own registry, mirror both images and set
`image.repository` and `mcp.image.repository` in the values.

## Prerequisites

| What | How to check |
|---|---|
| A chat model with tool calling | Azure AI Foundry: `scripts/setup-foundry.sh` ([foundry-setup.md](foundry-setup.md)) |
| (AKS) OIDC issuer and Workload Identity on the cluster | `az aks show -g my-cluster-rg -n my-cluster --query "[oidcIssuerProfile.enabled, securityProfile.workloadIdentity.enabled]"` → `[true, true]` |
| (Optional) External Secrets Operator, for the Teams webhook | `kubectl get crd externalsecrets.external-secrets.io secretstores.external-secrets.io` |
| (Optional) The secret with the Teams webhook URL | e.g. `teams-webhook-url` in `my-keyvault` ([MS Teams setup](../README.md#ms-teams-setup)) |
| Approval to send this cluster's data to the model | your organisation's data-processing rules ([data-processing.md](data-processing.md)) |
| (AKS) Rights | Contributor on the identity's resource group; Owner or User Access Administrator on the Key Vault and the Foundry resource |

## 1. Workload Identity (AKS)

A user-assigned managed identity, used for two things: External Secrets reads the
webhook URL from Key Vault with it, and the agent calls the model with it. Several
clusters (for example `dev` and `staging`) can share one identity, with one federated
credential per cluster. Keep the identity in a resource group that outlives the
clusters.

Create it with the script (it is safe to re-run):

```bash
az login
./scripts/setup-workload-identity.sh \
  "<subscription>" "<identity-resource-group>" "k8s-servant-identity" \
  "my-cluster-rg" "my-cluster" \
  "$(az keyvault show --name my-keyvault --query id -o tsv)" \
  "$(az cognitiveservices account show --name my-foundry \
       --resource-group my-foundry-rg --query id -o tsv)"
```

Run `./scripts/setup-workload-identity.sh` without arguments for the full list,
including the optional secret name and ServiceAccount namespace and name.

The script does the following:
- creates the identity in the cluster's region;
- creates (or re-points) the federated credential for
  `system:serviceaccount:k8s-servant:k8s-servant` on the cluster's OIDC issuer;
- gives the identity read access to the one Key Vault secret. On a vault that uses
  Azure RBAC, the role is scoped to that secret only. A vault with access policies
  cannot limit access to one secret, so the script stops there. It continues only
  when you accept vault-wide read access with `ALLOW_VAULT_WIDE_SECRET_READ=yes`;
- gives the identity *Cognitive Services OpenAI User* on the Foundry resource;
- prints the values snippet (client ID, tenant ID) and an equivalent Terraform snippet.

For a second cluster, run the same command with that cluster's resource group and name
as arguments 4 and 5: it finds the identity and roles, and adds the federated
credential.

Role assignments can take a few minutes to take effect.

**Without AKS or Workload Identity:** set `azureWorkloadIdentity.enabled: false` and
give the agent an API key from a Kubernetes Secret:

```bash
kubectl -n k8s-servant create secret generic my-llm-key --from-literal=api-key=<key>
```

```yaml
azureWorkloadIdentity:
  enabled: false
llmApiKey:
  existingSecret: my-llm-key   # mounted as FOUNDRY_API_KEY; never put the key in appConfig
```

The Teams webhook then needs a Secret you create yourself (or with any External
Secrets provider).

## 2. Network

If the cluster's egress is restricted, the pod needs HTTPS (443) to:
- the model endpoint, e.g. `my-foundry.openai.azure.com`;
- `login.microsoftonline.com`, for Entra ID tokens (Workload Identity);
- the Teams webhook host;
- your Prometheus and Alertmanager, if they are outside the cluster.

If `k8s-servant doctor` in the pod shows a timeout for the model, open the egress or
use a private endpoint ([foundry-setup.md](foundry-setup.md#7-step-5--networking)).

## 3. Values for the cluster

Start from the examples in this repository:

| File | Holds |
|---|---|
| `charts/k8s-servant/values.yaml` | chart defaults, every key commented |
| `examples/knowledge-values.yaml` | an example platform description and cluster notes: copy and describe *your* platform |
| `examples/values-azure.yaml` | AKS + Azure AI Foundry + Workload Identity + Key Vault through External Secrets |

The minimum for the Azure path is the cluster name, the model endpoint and deployment,
and the client ID the script printed:

```yaml
azureWorkloadIdentity:
  enabled: true
  clientId: "<client id>"
  tenantId: "00000000-0000-0000-0000-000000000000"

appConfig:
  CLUSTER_NAME: my-cluster
  FOUNDRY_ENDPOINT: https://my-foundry.openai.azure.com/
  FOUNDRY_DEPLOYMENT: k8s-servant
  PROMETHEUS_URL: http://prometheus-operated.monitoring:9090
  ALERTMANAGER_URL: http://alertmanager-operated.monitoring:9093
  MORNING_REPORT_TIMEZONE: UTC

teamsWebhook:
  enabled: true
  keyVaultUrl: https://my-keyvault.vault.azure.net/
  keyVaultSecretName: teams-webhook-url
```

With Workload Identity enabled, the chart refuses to render with an empty client ID.

Values files are applied in order (later wins). A common layout is one shared file for
the platform description and one file per cluster. The CLI reads the same files
(`k8s-servant --values …`), so a question asked on a laptop gets the same knowledge as
the pod.

## 4. Install with Helm

```bash
helm install k8s-servant oci://ghcr.io/k8s-servant/charts/k8s-servant \
  -n k8s-servant --create-namespace \
  -f knowledge-values.yaml -f my-values.yaml
```

Pin a version with `--version <x.y.z>`; upgrade with `helm upgrade` and the same
arguments. `helm template` with the same values shows what will be applied.

## 5. Install with Flux

`examples/flux-helmrelease.yaml` contains an `OCIRepository` pointing at
`oci://ghcr.io/k8s-servant/charts/k8s-servant` (with cosign signature verification) and
a `HelmRelease` that installs the chart into the namespace `k8s-servant`, with values
from two ConfigMaps (the platform description, then this cluster; later wins). Copy it
into your GitOps repository, generate the ConfigMaps (for example with a Kustomize
`configMapGenerator`), and let Flux reconcile it. If you use External Secrets, add a
`dependsOn` on its HelmRelease so the CRDs exist before the agent is installed.

## 6. Check the deployment

All of these are read-only:

```bash
# The release and the pod
helm -n k8s-servant status k8s-servant                    # or: kubectl -n flux-system get helmrelease
kubectl -n k8s-servant get pods                          # 2/2 Running
kubectl -n k8s-servant get externalsecret                # SecretSynced, Ready=True (if enabled)

# The agent's view of its own setup: model, MCP, kubectl, RBAC, Prometheus, Teams
kubectl -n k8s-servant exec deploy/k8s-servant -c agent -- k8s-servant doctor

# The scheduler: MCP tools listed, first check (always a full one) and its token use
kubectl -n k8s-servant logs deploy/k8s-servant -c agent --tail=100

# RBAC: what the agent may and may not do
SA=system:serviceaccount:k8s-servant:k8s-servant
kubectl auth can-i get secrets --as=$SA -A                 # no
kubectl auth can-i delete pods --as=$SA -A                 # no
kubectl auth can-i list nodes --as=$SA                     # yes
kubectl auth can-i list helmreleases.helm.toolkit.fluxcd.io --as=$SA -A   # yes (if Flux is installed)
```

Then:
- ask a question inside the pod (it shares memory with the scheduler):
  `kubectl -n k8s-servant exec -it deploy/k8s-servant -c agent -- k8s-servant ask "Anything unhealthy?"`;
- compare the first check's findings with what you see by hand;
- post once to Teams:
  `kubectl -n k8s-servant exec deploy/k8s-servant -c agent -- env TEAMS_POST_MODE=always k8s-servant check --post`;
- after a restart (`kubectl -n k8s-servant rollout restart deploy/k8s-servant`),
  open findings must stay open and not come back as new. Memory lives on the volume.

## When a cluster is recreated

A cluster that is destroyed and recreated (for example a short-lived test cluster) gets
a **new OIDC issuer**. A federated credential that still points to the old one makes
Entra ID refuse the pod's tokens: the model calls and the ExternalSecret fail with
`AADSTS70021` / `No matching federated identity record found`.

Re-run `scripts/setup-workload-identity.sh` with the same arguments: it finds the
credential and points it to the new issuer. If you recreate clusters with automation,
add that step there (the script prints a Terraform snippet).

The memory volume is destroyed with the cluster too, so after a recreation the first
check reports every open problem as new. Consider `TEAMS_POST_MODE: off` for such
clusters.

## Day-2 operations

| Task | How |
|---|---|
| **Upgrade** | `helm upgrade` to the new chart version (or bump the version in the HelmRelease). The chart version pins the matching image. Read the CHANGELOG first while the project is before `v1.0` |
| **Roll back** | `helm rollback k8s-servant` (or revert the version in Git with Flux) |
| **Change a setting** | `appConfig` in your values; the pod restarts by itself (ConfigMap checksum) |
| **Change what the agent knows** | `knowledge` in the values files ([knowledge-and-skills.md](knowledge-and-skills.md)); the pod restarts by itself |
| **Change the kubernetes-mcp-server version** | `mcp.image.tag` in the values. The chart's default is the tested version |
| **Stop posting to Teams** | `teamsWebhook.enabled: false` (no ESO objects, `TEAMS_POST_MODE=off`), or `appConfig.TEAMS_POST_MODE: off` |
| **Read or edit learned notes** | `kubectl -n k8s-servant exec deploy/k8s-servant -c agent -- k8s-servant notes`; editing: [memory.md](memory.md#learned-notes) |
| **Copy the memory out** | `kubectl -n k8s-servant cp -c agent <pod>:/data ./agent-data` |
| **Uninstall** | `helm uninstall k8s-servant -n k8s-servant` (or remove the HelmRelease). Everything is deleted **except** the PersistentVolumeClaim (`helm.sh/resource-policy: keep`). Delete it by hand to forget all findings: `kubectl -n k8s-servant delete pvc k8s-servant-data` |
| **Pod restarts all the time** | The liveness probe fails when the scheduler has not touched `/tmp/heartbeat` for 5 minutes. Read `kubectl logs --previous`; see [troubleshooting.md](troubleshooting.md#in-the-cluster) |
