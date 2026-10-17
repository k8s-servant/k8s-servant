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
├── ServiceAccount k8s-servant       optional cloud identity via annotations (Azure Workload Identity, IRSA, ...)
├── ConfigMap k8s-servant-config     settings (envFrom), from appConfig
├── ConfigMap k8s-servant-knowledge  environment.md, cluster.md, skill__*.md → /knowledge
├── ConfigMap k8s-servant-mcp        the MCP server's config.toml
├── PersistentVolumeClaim k8s-servant-data   1 Gi, /data: memory.db + notes.md (kept on uninstall)
├── (your Secrets)                   API key and Teams webhook, created by you; mounted as files
├── (extraObjects)                  anything else you install with the release, e.g. an ExternalSecret
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
| (Optional) The Teams webhook URL, as a Kubernetes Secret or in your secret store | [MS Teams setup](../README.md#ms-teams-setup); see [Credentials](#credentials-bring-your-own-secrets) |
| (Optional, Azure example) External Secrets Operator, to sync it from Key Vault | `kubectl get crd externalsecrets.external-secrets.io secretstores.external-secrets.io` |
| Approval to send this cluster's data to the model | your organisation's data-processing rules ([data-processing.md](data-processing.md)) |
| (AKS) Rights | Contributor on the identity's resource group; Owner or User Access Administrator on the Key Vault and the Foundry resource |

## 1. Workload Identity (AKS)

A user-assigned managed identity, used for two things: the agent calls the model with
it, and (in `examples/values-azure.yaml`) External Secrets reads the webhook URL from
Key Vault with it. Several
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

**Without AKS or Workload Identity** (the chart default, `azureWorkloadIdentity.enabled:
false`), give the agent an API key from a Secret (`llmApiKey.existingSecret`, see
[Credentials](#credentials-bring-your-own-secrets)). On EKS or GKE, the cloud identity
annotations go into `serviceAccount.annotations` (for example
`eks.amazonaws.com/role-arn`).

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
| `examples/values-azure.yaml` | **The complete Azure setup:** Azure AI Foundry with Workload Identity, and the Teams webhook synced from Key Vault by External Secrets (its objects installed through `extraObjects`) |
| `examples/values-azure-csi.yaml` | Azure **without any Kubernetes Secret**: Workload Identity, and the Teams webhook mounted straight from Key Vault by the Secrets Store CSI driver |
| `examples/values-minimal.yaml` | Any cluster: an API key and the Teams webhook from Secrets created with kubectl, nothing cloud-specific |

The chart requires only `appConfig.CLUSTER_NAME` and `appConfig.FOUNDRY_ENDPOINT`.
Workload Identity is off in the chart defaults, so the Azure example turns it on
explicitly; with it enabled, the chart refuses to render with an empty client ID.

### Credentials: bring your own Secrets

The chart never creates Secrets and does not depend on any secret tool. The agent
reads each credential from a **file** (never an environment variable), and you choose
where the file comes from:

- `existingSecret`: a Kubernetes Secret you create; the chart mounts it; or
- `file`: the absolute path of a file you mount yourself with `extraVolumes` and
  `extraVolumeMounts` (Secrets Store CSI driver, Vault Agent, ...). No Kubernetes
  Secret is involved.

Set one or the other, not both. With `file`, the mount is yours: the chart checks only
that the path is absolute, so a path without a matching `extraVolumeMounts` entry shows
up at runtime (the agent stops with "unreadable file"). If you add a Secret to the environment yourself, for
example through `extraEnvFrom`, that is your choice; the chart's own wiring never does it.

| Credential | Values | Read by the agent from |
|---|---|---|
| Model API key (not needed with Workload Identity) | `llmApiKey.existingSecret` (key `llmApiKey.secretKey`, default `api-key`) or `llmApiKey.file` | `FOUNDRY_API_KEY_FILE` |
| Teams webhook URL | `teamsWebhook.existingSecret` (key `teamsWebhook.secretKey`, default `webhook-url`) or `teamsWebhook.file` | `TEAMS_WEBHOOK_URL_FILE` |

How the Secret gets there is your choice:

- **kubectl:** put the value in a file named after the key, then
  `kubectl -n k8s-servant create secret generic teams-webhook --from-file=./webhook-url`
  (see `examples/values-minimal.yaml`).
- **External Secrets Operator:** put a `SecretStore` and an `ExternalSecret` that
  creates the Secret into `extraObjects`, so they are installed with the release.
  `examples/values-azure.yaml` does this for Azure Key Vault with Workload Identity.
- **Sealed Secrets, SOPS, Vault Agent, ...:** create the Secret as you usually do, and
  set its name.
- **Secrets Store CSI driver:** mount the volume with `extraVolumes` and
  `extraVolumeMounts`, put the `SecretProviderClass` into `extraObjects`, and point
  `file` at the mounted file. `examples/values-azure-csi.yaml` does this for Azure Key
  Vault: nothing is stored in a Kubernetes Secret.

### Extension points

For anything the chart does not model, the agent container takes `extraEnv`,
`extraEnvFrom`, `extraVolumes` and `extraVolumeMounts`, and `extraObjects` installs
extra manifests with the release. Typical uses: an HTTPS proxy, a corporate CA bundle,
site-wide settings from a ConfigMap. Two rules keep the safety promises: `extraEnv`
and `extraEnvFrom` cannot override what the chart sets (in-cluster paths, the
credential files), and an `extraEnv` name that looks like a credential (`API_KEY`,
`WEBHOOK_URL`, `TOKEN`, `PASSWORD`, `SECRET`, any case) is accepted only through
`valueFrom`, never as a plain value. Environment variables and volumes cannot change
RBAC. `extraObjects` is the exception: it installs any manifest you give it, so a Role
or binding there could widen the agent's access ([security.md](security.md#in-the-cluster)).

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
| **Stop posting to Teams** | Empty `teamsWebhook.existingSecret` (no mount, `TEAMS_POST_MODE=off`), or `appConfig.TEAMS_POST_MODE: off` |
| **Rotate a credential** | Update the Secret (or let your secret tool do it). The Teams URL is read on every post, so the new one is used once the kubelet refreshes the mounted file (about a minute). The API key is read at start-up: `kubectl -n k8s-servant rollout restart deploy/k8s-servant` |
| **Read or edit learned notes** | `kubectl -n k8s-servant exec deploy/k8s-servant -c agent -- k8s-servant notes`; editing: [memory.md](memory.md#learned-notes) |
| **Copy the memory out** | `kubectl -n k8s-servant cp -c agent <pod>:/data ./agent-data` |
| **Uninstall** | `helm uninstall k8s-servant -n k8s-servant` (or remove the HelmRelease). Everything is deleted **except** the PersistentVolumeClaim (`helm.sh/resource-policy: keep`). Delete it by hand to forget all findings: `kubectl -n k8s-servant delete pvc k8s-servant-data` |
| **Pod restarts all the time** | The liveness probe fails when the scheduler has not touched `/tmp/heartbeat` for 5 minutes. Read `kubectl logs --previous`; see [troubleshooting.md](troubleshooting.md#in-the-cluster) |
