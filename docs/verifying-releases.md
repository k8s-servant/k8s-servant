# Verifying releases

Every release publishes two artifacts to the GitHub Container Registry, and both are
signed:

| Artifact | Reference |
|---|---|
| Agent image | `ghcr.io/k8s-servant/k8s-servant:<version>` |
| Helm chart | `oci://ghcr.io/k8s-servant/charts/k8s-servant` (version `<version>`) |

The signature lets you check two things before you run anything: the artifact was built
by **this project's release workflow**, not by a fork or someone else, and it has **not
changed** since it was built. This page explains how the signing works and how to check
it, by hand or automatically in a cluster.

## Contents

- [How the signing works](#how-the-signing-works)
- [The identity to check](#the-identity-to-check)
- [Verify by hand](#verify-by-hand)
- [Enforce it in the cluster](#enforce-it-in-the-cluster)
- [SBOM and provenance](#sbom-and-provenance)
- [For maintainers](#for-maintainers)

## How the signing works

Releases are signed with [cosign](https://github.com/sigstore/cosign) in **keyless**
mode, using the public [Sigstore](https://www.sigstore.dev/) services. There is no
long-lived private key: none in the repository, in CI secrets, or on a maintainer's
machine. Each release gets a fresh, short-lived identity instead:

```
release workflow (runs when the release PR is merged)
   │
   ├─1─▶ GitHub issues an OIDC token that says: "workflow
   │      k8s-servant/k8s-servant/.github/workflows/release-please.yml on refs/heads/main"
   │
   ├─2─▶ Fulcio, Sigstore's certificate authority, checks the token and issues a
   │      certificate valid for about 10 minutes, with that identity written inside
   │
   ├─3─▶ cosign creates a throwaway key pair, signs the artifact's digest
   │      (sha256:…, the exact bytes, not the tag) and discards the key
   │
   └─4─▶ the signature and certificate are recorded in Rekor, Sigstore's public,
          append-only transparency log
```

The signature is stored in GHCR next to the artifact.

Why you can trust this without a key:

- **Nothing to steal.** The private key exists for seconds, inside the GitHub runner.
- **Forks can't sign as this project.** A fork's workflow gets a certificate naming
  the fork's repository, and verification rejects it.
- **Signing is public.** Every signature is in the Rekor log, which anyone can search
  ([search.sigstore.dev](https://search.sigstore.dev/)) and nobody can rewrite.
  A signature made outside the release workflow would be visible there.
- **Any change breaks the signature.** It covers the digest, so a modified image, or a
  different image pushed under the same tag, fails verification.

## The identity to check

Verification means: "accept this artifact only if it was signed by **this** identity,
vouched for by **this** issuer."

| Field | Value |
|---|---|
| Certificate identity (subject) | `https://github.com/k8s-servant/k8s-servant/.github/workflows/release-please.yml@refs/heads/main` |
| OIDC issuer | `https://token.actions.githubusercontent.com` |

Match the identity **exactly**. A loose pattern such as
`^https://github.com/k8s-servant/k8s-servant/` also accepts signatures from any other
workflow or branch in the repository, which is weaker than necessary.

## Verify by hand

Install [cosign](https://docs.sigstore.dev/cosign/system_config/installation/) v2 or
newer. Then:

```bash
# The image
cosign verify ghcr.io/k8s-servant/k8s-servant:<version> \
  --certificate-identity "https://github.com/k8s-servant/k8s-servant/.github/workflows/release-please.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"

# The chart
cosign verify ghcr.io/k8s-servant/charts/k8s-servant:<version> \
  --certificate-identity "https://github.com/k8s-servant/k8s-servant/.github/workflows/release-please.yml@refs/heads/main" \
  --certificate-oidc-issuer "https://token.actions.githubusercontent.com"
```

cosign checks that:

1. the signature matches this exact digest, so nothing was altered;
2. the certificate was issued by Sigstore's CA and was valid at signing time. The
   Rekor log entry proves the time, so an expired certificate is expected and fine;
3. the identity and issuer in the certificate are the ones you asked for.

On success it prints the verified signatures as JSON and exits with status 0. Any
other result means: don't use the artifact.

`helm install` doesn't check cosign signatures itself, so verify the chart first, then
install the same version:

```bash
helm install k8s-servant oci://ghcr.io/k8s-servant/charts/k8s-servant \
  --version <version> -n k8s-servant --create-namespace -f my-values.yaml
```

## Enforce it in the cluster

An admission controller can reject pods whose image is not signed by the release
workflow, so the check happens on every deployment, not only when someone remembers.

**Kyverno** (`ClusterPolicy` with `verifyImages`, as in the Kyverno 1.13+ docs; newer
releases also offer the CEL-based `ImageValidatingPolicy`):

```yaml
apiVersion: kyverno.io/v1
kind: ClusterPolicy
metadata:
  name: verify-k8s-servant-image
spec:
  webhookConfiguration:
    timeoutSeconds: 30
  rules:
    - name: signed-by-release-workflow
      match:
        any:
          - resources:
              kinds: [Pod]
      verifyImages:
        - imageReferences: ["ghcr.io/k8s-servant/k8s-servant*"]
          failureAction: Enforce
          attestors:
            - entries:
                - keyless:
                    issuer: https://token.actions.githubusercontent.com
                    subject: https://github.com/k8s-servant/k8s-servant/.github/workflows/release-please.yml@refs/heads/main
                    rekor:
                      url: https://rekor.sigstore.dev
```

**Sigstore policy-controller:**

```yaml
apiVersion: policy.sigstore.dev/v1beta1
kind: ClusterImagePolicy
metadata:
  name: k8s-servant
spec:
  images:
    - glob: "ghcr.io/k8s-servant/**"
  authorities:
    - keyless:
        url: https://fulcio.sigstore.dev
        identities:
          - issuer: https://token.actions.githubusercontent.com
            subject: https://github.com/k8s-servant/k8s-servant/.github/workflows/release-please.yml@refs/heads/main
```

GitOps tools that pull the chart can verify it on the way in with the same identity
and issuer. For example, `examples/flux-helmrelease.yaml` does this for Flux.

If you mirror the images into your own registry, copy the signatures too
(`oras cp -r`; `cosign copy` did not copy them in our test), or verify before mirroring
and sign the copy with your own identity. See
[running-from-a-mirror.md](running-from-a-mirror.md#mirror-a-release).

The MCP sidecar image (`ghcr.io/containers/kubernetes-mcp-server`) comes from its own
upstream project and is not signed by this release workflow. Keep it out of the policy
above, or add a separate rule for it.

## SBOM and provenance

Each release image also carries two attestations, attached by the build and covered
by the signed digest:

- **SBOM:** every package inside the image, in SPDX format. Feed it to a vulnerability
  scanner, or check licenses.
- **Provenance (SLSA, `mode=max`):** how the image was built: the source repository
  and commit, the workflow, and the build parameters.

```bash
docker buildx imagetools inspect ghcr.io/k8s-servant/k8s-servant:<version> --format '{{ json .SBOM }}'
docker buildx imagetools inspect ghcr.io/k8s-servant/k8s-servant:<version> --format '{{ json .Provenance }}'
```

## For maintainers

- The identity above is derived from the workflow **file path** and the **branch** the
  release runs on. Renaming `.github/workflows/release-please.yml`, or releasing from
  another branch, changes it, and every user's policy stops matching. If that is ever
  necessary, announce it in the release notes and update this page and
  `examples/flux-helmrelease.yaml` in the same PR.
- The identity names the workflow file and the branch, **not the job**. Any job in
  `release-please.yml` with `id-token: write` can sign as this identity, so keep that
  permission limited to the `publish image` and `publish chart` jobs, and review any
  new job in that file with this in mind. See
  [ci-and-releases.md](ci-and-releases.md#release-pleaseyml-versions-and-published-artifacts).
- The SBOM and provenance are covered by the signature because the workflow signs the
  **image index** digest (`steps.build.outputs.digest` of the multi-arch build), and
  the index references the attestation manifests. Keep signing that digest, not a
  per-platform one.
