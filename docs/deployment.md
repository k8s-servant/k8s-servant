# Deploying to a cluster

How k8s-servant gets into a Kubernetes cluster and how to run it there. The chart is
plain Helm and works on any conformant cluster (AKS, EKS, GKE, on-premises, kind) and
with every model provider ([model-providers.md](model-providers.md)). The steps below
are the same everywhere; what differs per cloud (for example a cloud identity instead of
an API key) is in [Cloud-specific setup](#cloud-specific-setup).

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

If your cluster only admits images from your own registry, mirror both images and the
chart, and set `image.repository` and `mcp.image.repository` in the values.
[running-from-a-mirror.md](running-from-a-mirror.md) shows how to copy them with their
signatures, and how to keep your settings in a private repository without a fork.

## Before you start

| What | Why |
|---|---|
| A cluster and Helm 3.8 or newer | The chart is an OCI artifact |
| A chat model with tool calling | Pick a provider and get its credential: [model-providers.md](model-providers.md) |
| (Optional) A Teams webhook URL | For the morning report and alerts: [MS Teams setup](teams-setup.md) |
| A default StorageClass, or `persistence.storageClassName` | For the 1 Gi memory volume |
| Approval to send this cluster's data to the model | Your organisation's data-processing rules ([data-processing.md](data-processing.md)) |

## Step 1: Draft the knowledge

Draft the cluster's knowledge files before you install, from a laptop whose `kubectl`
points at the cluster:

```bash
k8s-servant onboard                 # writes k8s-servant-onboard/: up to two values files plus settings.env
```

The scan is read-only and never reads Secrets. Review the draft (every
`TODO(onboard):` line), then use the files as values in
[step 4](#step-4-write-the-values): `knowledge-values.yaml` (written only when you
say the cluster is representative) is the platform description shared by every
cluster, `<cluster>.yaml` holds this cluster's notes and suggested `appConfig`
settings. `settings.env` repeats those settings for the CLI on your laptop. The full
guide: [onboarding.md](onboarding.md).

Once installed, the agent re-scans before each morning report and lists what changed
since the last scan. To take a fresh draft from the running pod:
`kubectl -n k8s-servant exec deploy/k8s-servant -c agent -- k8s-servant onboard --show`.

## Step 2: Give the agent access to the model

One of these, depending on the provider:

- **An API key** (Anthropic, OpenAI, an OpenAI-compatible server, Microsoft Foundry
    with a key): put it in a Kubernetes Secret, or a file your secret tool mounts, and
    point `llmApiKey` at it ([Credentials](#credentials-bring-your-own-secrets)). This
    works on every cluster.
- **Nothing**, for a model in your network without a key, such as an in-cluster Ollama.
- **A cloud identity instead of a key**, for Microsoft Foundry on AKS with Workload
    Identity: see [AKS](#aks).

## Step 3: Network

If the cluster's egress is restricted, the pod needs HTTPS (443) to:

- the model endpoint (`LLM_ENDPOINT`, or the provider's default), e.g. `api.anthropic.com`,
    `api.openai.com` or `my-foundry.openai.azure.com`; nothing for an in-cluster Ollama;
- your identity provider, when the model uses a cloud identity
    (`login.microsoftonline.com` for Foundry with Workload Identity);
- the Teams webhook host;
- your Prometheus and Alertmanager, if they are outside the cluster.

If `k8s-servant doctor` in the pod shows a timeout for the model, open the egress, or
use a private endpoint where your provider offers one (Foundry:
[setup guide](providers/foundry/setup.md#7-step-5-networking)).

## Step 4: Write the values

Start from the examples in this repository:

| File | Holds |
|---|---|
| `charts/k8s-servant/values.yaml` | Chart defaults, every key commented |
| `examples/knowledge-values.yaml` | An example platform description and cluster notes: copy and describe *your* platform, or start from the [first-run draft](#step-1-draft-the-knowledge) |
| `examples/values-minimal.yaml` | Any cluster: `openai-compatible` with an API key and the Teams webhook from Secrets created with kubectl |
| `examples/values-anthropic.yaml` | Any cluster: Claude from Anthropic, key from a Secret |
| `examples/values-openai.yaml` | Any cluster: OpenAI, key from a Secret |
| `examples/values-ollama.yaml` | Any cluster: an in-cluster Ollama, no key |
| `examples/values-azure.yaml` | AKS: Microsoft Foundry with Workload Identity, the Teams webhook synced from Key Vault by External Secrets ([AKS](#aks)) |
| `examples/values-azure-csi.yaml` | AKS without any Kubernetes Secret: Workload Identity, the Teams webhook mounted from Key Vault by the Secrets Store CSI driver |
| `examples/values-azure-claude.yaml` | AKS: Claude on Foundry with Workload Identity |

Every provider has a full recipe in [model-providers.md](model-providers.md).

The chart requires `appConfig.CLUSTER_NAME` and `appConfig.LLM_MODEL`, plus
`appConfig.LLM_ENDPOINT` for `foundry` and `openai-compatible`. The chart's
`values.yaml` sets `LLM_PROVIDER` to `foundry` by default, so set
`appConfig.LLM_PROVIDER` yourself for any other provider.

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
| Model API key (not needed with Workload Identity) | `llmApiKey.existingSecret` (key `llmApiKey.secretKey`, default `api-key`) or `llmApiKey.file` | `LLM_API_KEY_FILE` |
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

## Step 5: Install

### With Helm

```bash
helm install k8s-servant oci://ghcr.io/k8s-servant/charts/k8s-servant \
  -n k8s-servant --create-namespace \
  -f knowledge-values.yaml -f my-values.yaml
```

Pin a version with `--version <x.y.z>`; upgrade with `helm upgrade` and the same
arguments. `helm template` with the same values shows what will be applied.

### With Flux (optional)

`examples/flux-helmrelease.yaml` contains an `OCIRepository` pointing at
`oci://ghcr.io/k8s-servant/charts/k8s-servant` (with cosign signature verification) and
a `HelmRelease` that installs the chart into the namespace `k8s-servant`, with values
from two ConfigMaps (the platform description, then this cluster; later wins). Copy it
into your GitOps repository, generate the ConfigMaps (for example with a Kustomize
`configMapGenerator`), and let Flux reconcile it. If you use External Secrets, add a
`dependsOn` on its HelmRelease so the CRDs exist before the agent is installed.

## Step 6: Check the deployment

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
| **Cluster recreated** | The memory volume goes with the cluster, so the first check reports every open problem as new. Consider `appConfig.TEAMS_POST_MODE: off` for short-lived clusters. On AKS, also re-point the federated credential ([AKS](#when-the-cluster-is-recreated)) |
| **Pod restarts all the time** | The liveness probe fails when the scheduler has not touched `/tmp/heartbeat` for 5 minutes. Read `kubectl logs --previous`; see [troubleshooting.md](troubleshooting.md#in-the-cluster) |

## Cloud-specific setup

### AKS

The fully worked Azure path: Microsoft Foundry with Workload Identity (no API key), and
the Teams webhook from Key Vault. Values: `examples/values-azure.yaml`,
`examples/values-azure-csi.yaml` or `examples/values-azure-claude.yaml`.

**Before you start:**

| What | How to check |
|---|---|
| OIDC issuer and Workload Identity on the cluster | `az aks show -g my-cluster-rg -n my-cluster --query "[oidcIssuerProfile.enabled, securityProfile.workloadIdentity.enabled]"` → `[true, true]` |
| A Foundry resource with a deployed model | `scripts/setup-foundry.sh` ([Foundry setup guide](providers/foundry/setup.md)) |
| (Optional) External Secrets Operator, to sync the webhook from Key Vault | `kubectl get crd externalsecrets.external-secrets.io secretstores.external-secrets.io` |
| Rights | Contributor on the identity's resource group; Owner or User Access Administrator on the Key Vault and the Foundry resource |

#### Workload Identity

A user-assigned managed identity, used for two things: the agent calls the model with
it, and (in `examples/values-azure.yaml`) External Secrets reads the webhook URL from
Key Vault with it. Several clusters (for example `dev` and `staging`) can share one
identity, with one federated credential per cluster. Keep the identity in a resource group that outlives the
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
- gives the identity *Cognitive Services OpenAI User* on the Foundry resource, which
    covers OpenAI models only; for Claude and other non-OpenAI models set
    `MODEL_ROLE="Cognitive Services User"` when running the script;
- prints the values snippet (client ID, tenant ID) and an equivalent Terraform snippet.

For a second cluster, run the same command with that cluster's resource group and name
as arguments 4 and 5: it finds the identity and roles, and adds the federated
credential.

Role assignments can take a few minutes to take effect.

Workload Identity is off in the chart defaults, so the Azure examples turn it on
explicitly; with it enabled, the chart refuses to render with an empty client ID.

#### When the cluster is recreated

A cluster that is destroyed and recreated (for example a short-lived test cluster) gets
a **new OIDC issuer**. A federated credential that still points to the old one makes
Entra ID refuse the pod's tokens: the model calls and the ExternalSecret fail with
`AADSTS70021` / `No matching federated identity record found`.

Re-run `scripts/setup-workload-identity.sh` with the same arguments: it finds the
credential and points it to the new issuer. If you recreate clusters with automation,
add that step there (the script prints a Terraform snippet).

### EKS

The project has not tested an EKS install yet; reports are welcome.

- **Model:** use an API key from a Secret (Step 2). Amazon Bedrock is not a built-in
    provider; an OpenAI-compatible gateway in front of it (for example LiteLLM) works
    as `openai-compatible`.
- **Secrets from AWS:** with External Secrets and AWS Secrets Manager or Parameter
    Store, put the `SecretStore` and `ExternalSecret` into `extraObjects`. For IAM roles
    for service accounts (IRSA), add `eks.amazonaws.com/role-arn` to
    `serviceAccount.annotations`; EKS Pod Identity needs no annotation.

### GKE

The project has not tested a GKE install yet; reports are welcome.

- **Model:** use an API key from a Secret (Step 2). Vertex AI is not a built-in
    provider; an OpenAI-compatible gateway in front of it (for example LiteLLM) works
    as `openai-compatible`.
- **Secrets from Google Cloud:** with External Secrets and Secret Manager, put the
    `SecretStore` and `ExternalSecret` into `extraObjects`. For Workload Identity
    through a Google service account, add `iam.gke.io/gcp-service-account` to
    `serviceAccount.annotations`.

### Other clusters

On-premises clusters, kind and others need nothing cloud-specific: an API key from a
Secret (or an in-cluster Ollama) and a StorageClass for the memory volume. To run from
your own registry, see [running-from-a-mirror.md](running-from-a-mirror.md).
