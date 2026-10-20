# Running from a mirror

How an organisation runs k8s-servant from its own registry and keeps its own settings in
a private repository, without forking the code.

The code lives in this repository only. Your side holds two things: **copies of the
released artifacts** in a registry you control, and a **private repository with
configuration** (values, the platform description, your own skills). Upgrading means
mirroring a new version and changing one version number.

## Contents

- [The idea](#the-idea)
- [What to mirror](#what-to-mirror)
- [Mirror a release](#mirror-a-release)
- [Install from the mirror](#install-from-the-mirror)
- [Enforce signatures on the mirror](#enforce-signatures-on-the-mirror)
- [The private configuration repository](#the-private-configuration-repository)
- [Upgrading](#upgrading)
- [When you need a change](#when-you-need-a-change)
- [Why not a fork](#why-not-a-fork)

## The idea

```
github.com/k8s-servant/k8s-servant        public: code, issues, releases
   │ release vX.Y.Z: signed image + signed chart on ghcr.io
   ▼
your registry (e.g. myregistry.example.com)
   │ the same image, chart and MCP sidecar, signatures included, same digests
   ▼
your private config repository
   │ values per cluster, platform description, extra skills, deployment (helm or Flux)
   ▼
your clusters                              pull only from your registry
```

Nothing company-specific goes upstream, and no code lives in your repository.

## What to mirror

| Artifact | Upstream | Signed by this project |
|---|---|---|
| Agent image | `ghcr.io/k8s-servant/k8s-servant:<version>` | Yes, with SBOM and provenance attached |
| Helm chart | `oci://ghcr.io/k8s-servant/charts/k8s-servant` (version `<version>`) | Yes |
| MCP sidecar image | `ghcr.io/containers/kubernetes-mcp-server:<tag>` | No: a separate upstream project. The chart pins the tag it was tested with (`mcp.image.tag`) |

The image and the chart always share a version number.

## Mirror a release

You need [oras](https://oras.land/docs/installation) and
[cosign](https://docs.sigstore.dev/cosign/system_config/installation/) v2 or newer
(tested with oras 1.3 and cosign 3.1), plus write access to your registry
(`oras login myregistry.example.com`; for Azure Container Registry,
`az acr login --name myregistry` also works, because oras reads the same credentials).

```bash
VERSION=0.3.0
MIRROR=myregistry.example.com/k8s-servant
IDENTITY="https://github.com/k8s-servant/k8s-servant/.github/workflows/release-please.yml@refs/heads/main"
ISSUER="https://token.actions.githubusercontent.com"

# 1. Check the upstream signatures before copying anything.
cosign verify "ghcr.io/k8s-servant/k8s-servant:${VERSION}" \
  --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" > /dev/null
cosign verify "ghcr.io/k8s-servant/charts/k8s-servant:${VERSION}" \
  --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" > /dev/null

# 2. Copy the image and the chart with everything attached to them (-r).
oras cp -r "ghcr.io/k8s-servant/k8s-servant:${VERSION}"        "${MIRROR}/k8s-servant:${VERSION}"
oras cp -r "ghcr.io/k8s-servant/charts/k8s-servant:${VERSION}" "${MIRROR}/charts/k8s-servant:${VERSION}"

# 3. Copy the MCP sidecar at the tag this chart version pins.
MCP_TAG=$(helm show values oci://ghcr.io/k8s-servant/charts/k8s-servant \
  --version "$VERSION" --jsonpath '{.mcp.image.tag}')
oras cp "ghcr.io/containers/kubernetes-mcp-server:${MCP_TAG}" "${MIRROR}/kubernetes-mcp-server:${MCP_TAG}"

# 4. Check that the copies still verify. Same identity: the signatures were copied, not re-made.
cosign verify "${MIRROR}/k8s-servant:${VERSION}" \
  --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" > /dev/null
cosign verify "${MIRROR}/charts/k8s-servant:${VERSION}" \
  --certificate-identity "$IDENTITY" --certificate-oidc-issuer "$ISSUER" > /dev/null
```

`oras cp -r` keeps the digest, both platforms (`linux/amd64`, `linux/arm64`), the SBOM,
the provenance and the cosign signature. Releases store the signature as an OCI
referrer, attached to the digest. Two common ways of copying lose it:

- `docker pull` + `docker push` copies one platform and drops the signature and the
  attestations.
- `cosign copy` (tested with cosign 3.1) copied the image but not its signature, and
  `cosign verify` on the copy failed with "no signatures found".

If your tool of choice is different (a registry's import command, a pull-through
cache, a replication rule), run step 4 once to see whether it keeps the signature. If it
doesn't, verify upstream before copying and sign the copy with your own identity.

Run this from a pipeline in your configuration repository, so every mirrored version
has a log.

A security review that wants to know exactly which commit and build run produced a
mirrored version can read it from the copied signature, using the commands in
[verifying-releases.md](verifying-releases.md#what-the-certificate-tells-you) with the
mirror's reference.

## Install from the mirror

Point both images at the mirror in your values file. Leave the tags alone: the agent
image follows the chart version, and the sidecar keeps the tag the chart pins.

```yaml
image:
  repository: myregistry.example.com/k8s-servant/k8s-servant
mcp:
  image:
    repository: myregistry.example.com/k8s-servant/kubernetes-mcp-server
```

Install the chart from the mirror:

```bash
helm install k8s-servant oci://myregistry.example.com/k8s-servant/charts/k8s-servant \
  --version 0.3.0 -n k8s-servant --create-namespace \
  -f knowledge-values.yaml -f my-cluster.yaml
```

With Flux, change `url` in the `OCIRepository` of `examples/flux-helmrelease.yaml` to
`oci://myregistry.example.com/k8s-servant/charts/k8s-servant`. To pin one version,
replace `ref.semver` with `ref.tag: 0.3.0` (set one of them, not both); to follow new
versions automatically, keep a `semver` range, but then only versions you have mirrored
are found. Keep the `verify` block as it is: the copied signature still names this
project's release workflow.

## Enforce signatures on the mirror

The admission policies in
[verifying-releases.md](verifying-releases.md#enforce-it-in-the-cluster) work on the
mirror unchanged, except for the image patterns. Change every one that names
ghcr.io: Kyverno's `imageReferences` becomes
`myregistry.example.com/k8s-servant/k8s-servant*`, and the policy-controller's `glob`
becomes `myregistry.example.com/k8s-servant/k8s-servant*` as well (the upstream glob
`ghcr.io/k8s-servant/**` would also catch the unsigned sidecar once it sits under the
same path in your mirror). The identity and the issuer stay the same.

The MCP sidecar is not signed by this project. Leave it out of that rule, or add your
own rule for it.

Checking a keyless signature needs Sigstore's public trust root. Kyverno and cosign
fetch it from Sigstore by default, so a cluster without internet access has to be
given that trust root another way; see the documentation of your policy engine.

## The private configuration repository

One repository holds everything specific to your organisation. An example layout:

```
k8s-servant-config/
├── knowledge-values.yaml        # platform description shared by all clusters
├── clusters/
│   ├── dev.yaml                 # CLUSTER_NAME, model endpoint, identity, Secret names, mirror
│   └── prod.yaml
├── skills/                      # your own SKILL.md files, passed in as knowledge.extraSkills
├── mirror.sh                    # the steps from "Mirror a release"
└── deploy/                      # your pipeline, or the Flux OCIRepository + HelmRelease
```

What goes where:

| Belongs in your repository | Stays upstream |
|---|---|
| Cluster names, registries, tenant and subscription IDs, hostnames | All code, the chart, the built-in skills |
| The platform description: networks, node pools, policies, schedules | Generic fixes and features (see [When you need a change](#when-you-need-a-change)) |
| Skills that only make sense for your platform | Skills any cluster could use |
| `existingSecret` names and how the Secrets get there | |

Start from `examples/values-azure.yaml` (or the example for your model provider) and
`examples/knowledge-values.yaml`. Values files are applied in order, later wins, and
the CLI reads the same files (`k8s-servant --values ...`), so a question asked on a
laptop gets the same knowledge as the pod. Credentials never go into these files: the
chart reads them from Secrets you create
([deployment.md](deployment.md#credentials-bring-your-own-secrets)).

## Upgrading

1. **Hear about the release.** On GitHub, Watch → Custom → Releases on this repository,
   or let a dependency bot watch the chart version in your configuration repository.
2. **Read the release notes.** Settings that are renamed or removed are listed under
   breaking changes, and the agent refuses to start with an old name, saying which name
   replaced it.
3. **Mirror** the new version (the steps above).
4. **Change the version** in your configuration repository, in one pull request.
5. **Roll out to a non-production cluster first.** `k8s-servant doctor` in the pod checks
   the model, MCP, kubectl, RBAC and Teams settings:

   ```bash
   kubectl -n k8s-servant exec deploy/k8s-servant -c agent -- k8s-servant doctor
   ```

6. Then the other clusters.

Rolling back is the same pull request reverted: the older version is still in your
mirror.

## When you need a change

Ask one question first: would another organisation want this too?

- **Yes:** open an issue or a pull request here, written generically, and use it after
  the next release. Bug fixes, new checks, new providers and new chart options all
  belong here.
- **No:** solve it in your configuration repository through the platform description,
  your own skills, or the chart's extension points (`extraEnv`, `extraEnvFrom`,
  `extraVolumes`, `extraVolumeMounts`, `extraObjects`;
  [deployment.md](deployment.md#extension-points)). A proxy, a corporate CA bundle or
  your own ExternalSecret all fit there.

If neither works, the chart is missing an extension point. That is a change for this
repository too, so open an issue.

Keep upstream pull requests free of your organisation's details: no real cluster names,
registries, IDs or hostnames in code, tests, examples or screenshots.

## Why not a fork

- **Drift.** Every release turns into a merge, and fixes arrive late.
- **Signatures.** Images built from a fork are not signed by this project's release
  workflow, so the policies above can't tell them from anyone else's.
- **Leaks.** Company details written into a fork tend to end up in a public pull
  request sooner or later.

If you are moving from an internal copy or an older version, note that the `FOUNDRY_*`
settings were renamed to `LLM_*`; the agent names each new setting when it finds an old
one.
