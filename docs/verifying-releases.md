# Verifying releases

Every release publishes two artifacts to the GitHub Container Registry, and both are
signed:

| Artifact | Reference |
|---|---|
| Agent image | `ghcr.io/k8s-servant/k8s-servant:<version>` |
| Helm chart | `oci://ghcr.io/k8s-servant/charts/k8s-servant` (version `<version>`) |

The Python package for the CLI goes to [PyPI](https://pypi.org/project/k8s-servant/)
with its own attestations ([The PyPI package](#the-pypi-package)).

The signature lets you check two things before you run anything: the artifact was built
by **this project's release workflow**, not by a fork or someone else, and it has **not
changed** since it was built. This page explains how the signing works and how to check
it, by hand or automatically in a cluster.

## Contents

- [How the signing works](#how-the-signing-works)
- [The identity to check](#the-identity-to-check)
- [Verify by hand](#verify-by-hand)
- [What the certificate tells you](#what-the-certificate-tells-you)
- [Enforce it in the cluster](#enforce-it-in-the-cluster)
- [SBOM and provenance](#sbom-and-provenance)
- [The PyPI package](#the-pypi-package)
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

## What the certificate tells you

`cosign verify` answers yes or no. A security review often wants more: what exactly
was trusted, and which build produced this artifact. Both answers are in the signing
certificate.

**What a successful verification rests on:**

1. **Sigstore's root of trust**, shipped with cosign and kept up to date through
      Sigstore's signed update channel (TUF). It vouches for Fulcio, the certificate
      authority, and for Rekor, the log.
2. **GitHub's OIDC tokens.** Only a workflow run of this repository's
      `release-please.yml` on `main` gets a token with that identity, and only that token
      gets a certificate with it. A fork or another branch gets a different identity.
3. **Whoever can change `main`.** The repository's ruleset requires a pull request and
      passing checks for `main` and blocks force pushes; organisation admins can bypass
      it. Tags `v*` are protected by a second ruleset.

The identity you pass to `cosign verify` is not a secret and not something to take on
faith: it is the repository address plus the workflow file and the branch. Once you
decide to trust `github.com/k8s-servant/k8s-servant`, the identity follows. A wrong
identity handed to you would name a different repository.

**Which build produced the artifact.** cosign 3 no longer prints the certificate, but
it is in the signature bundle attached to the artifact. With
[oras](https://oras.land/docs/installation), `jq` and `openssl`:

```bash
ARTIFACT=ghcr.io/k8s-servant/k8s-servant:<version>   # or ghcr.io/k8s-servant/charts/k8s-servant:<version>
REPO=${ARTIFACT%:*}

# The signature is attached to the artifact's digest as an OCI referrer. Other
# referrers may sit next to it, so pick the one that holds a Sigstore bundle.
for SIG in $(oras discover "$ARTIFACT" --format json | jq -r '.referrers[].digest'); do
  BUNDLE=$(oras manifest fetch "$REPO@$SIG" \
    | jq -r '.layers[]? | select(.mediaType | startswith("application/vnd.dev.sigstore.bundle")) | .digest')
  [ -n "$BUNDLE" ] && break
done
[ -n "$BUNDLE" ] || echo "no signature bundle found on $ARTIFACT" >&2

# The certificate inside the bundle.
oras blob fetch "$REPO@$BUNDLE" --output - \
  | jq -r '.verificationMaterial.certificate.rawBytes' | base64 -d \
  | openssl x509 -inform der -noout -text

# Its entry in the Rekor log.
oras blob fetch "$REPO@$BUNDLE" --output - | jq '.verificationMaterial.tlogEntries[0] | {logIndex, integratedTime}'
```

The fields to read, with the values from release 0.3.0:

| Certificate field | Meaning | 0.3.0 |
|---|---|---|
| Subject Alternative Name | The identity `cosign verify` checks | `https://github.com/k8s-servant/k8s-servant/.github/workflows/release-please.yml@refs/heads/main` |
| Validity | The certificate lived about 10 minutes | 2026-10-18 14:15:09 to 14:25:09 UTC |
| `1.3.6.1.4.1.57264.1.12` | Source repository | `https://github.com/k8s-servant/k8s-servant` |
| `1.3.6.1.4.1.57264.1.13` | Source commit | `ba1382b7328931547162f744697ef69aa7bb54cf` (the release commit) |
| `1.3.6.1.4.1.57264.1.14` | Branch | `refs/heads/main` |
| `1.3.6.1.4.1.57264.1.20` | What started the build | `push` |
| `1.3.6.1.4.1.57264.1.21` | The build run | `https://github.com/k8s-servant/k8s-servant/actions/runs/39134712884/attempts/1` |
| Rekor `logIndex` | The public log entry | `3312333839` (image), `3312341301` (chart) |

Open the run to read the build logs, open the commit to see the code that went in, and
look up the log index on [search.sigstore.dev](https://search.sigstore.dev/).

These commands only read what is attached; they don't check it. Run `cosign verify`
first: it proves that the bundle is genuine and belongs to this digest.

**What a valid signature does not prove:** that the code is safe. It proves where the
artifact came from and that it hasn't changed since. Trust in the code itself comes
from review, the [SBOM](#sbom-and-provenance) and vulnerability scans, and the branch
rules above.

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

## The PyPI package

The release workflow uploads the sdist and the wheel with
[trusted publishing](https://docs.pypi.org/trusted-publishers/): PyPI accepts the
workflow's short-lived OIDC token instead of a stored API token. The upload also
carries a [PEP 740](https://peps.python.org/pep-0740/) attestation per file, signed
through Sigstore in the same keyless way as the image, with the identity above. PyPI
shows them on each file's page. To check a file yourself:

```bash
uvx pypi-attestations verify pypi --repository https://github.com/k8s-servant/k8s-servant \
  pypi:k8s_servant-<version>-py3-none-any.whl
```

## For maintainers

- The identity above is derived from the workflow **file path** and the **branch** the
  release runs on. Renaming `.github/workflows/release-please.yml`, or releasing from
  another branch, changes it, and every user's policy stops matching. If that is ever
  necessary, announce it in the release notes and update this page and
  `examples/flux-helmrelease.yaml` in the same PR.
- The identity names the workflow file and the branch, **not the job**. Any job in
  `release-please.yml` with `id-token: write` can sign as this identity, so keep that
  permission limited to the `publish image`, `publish chart` and `publish to PyPI` jobs, and review any
  new job in that file with this in mind. See
  [ci-and-releases.md](ci-and-releases.md#release-pleaseyml-versions-and-published-artifacts).
- The SBOM and provenance are covered by the signature because the workflow signs the
  **image index** digest (`steps.build.outputs.digest` of the multi-arch build), and
  the index references the attestation manifests. Keep signing that digest, not a
  per-platform one.
