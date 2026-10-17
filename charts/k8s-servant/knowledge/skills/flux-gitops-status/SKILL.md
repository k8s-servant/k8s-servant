---
name: flux-gitops-status
description: Use when checking whether Flux GitOps is healthy - Kustomizations, HelmReleases, GitRepositories and other Flux sources that are not Ready, suspended, stalled or failing to reconcile - or when a component looks out of date or keeps being reverted.
---
# Flux GitOps status

Flux keeps the cluster in sync with Git. Its objects report their state in
`status.conditions`; `Ready=True` means the last reconciliation succeeded.

## Rules

- Read-only. Never suspend, resume or force a reconciliation; name the action for the administrators.
- First confirm Flux is installed: the API resources `kustomizations.kustomize.toolkit.fluxcd.io` and `helmreleases.helm.toolkit.fluxcd.io` must exist. If they do not, say so and stop.

## Quick check (used by the cluster health check)

1. List Kustomizations, HelmReleases and sources (GitRepository, OCIRepository, HelmRepository, HelmChart) in all namespaces.
2. For each object read the `Ready` condition and `spec.suspend`.
3. Report every object with `Ready=False`, `Stalled=True`, or `Ready=Unknown` for a long time, with the condition's `reason` and `message`.
4. Report suspended objects as info: they do not reconcile and usually do not alert.

## Deeper investigation of one object

- Read the full status: all conditions; `lastAppliedRevision` versus `lastAttemptedRevision` (if they differ, the newest revision fails); for HelmReleases the history and failure counters.
- Typical reasons:
  - Source not ready / `ArtifactFailed`: Git or registry access (credentials, network). Check the source object.
  - `BuildFailed` / `ReconciliationFailed` on a Kustomization: invalid manifests in Git; the message names the file or object.
  - `InstallFailed` / `UpgradeFailed` on a HelmRelease: a chart or values problem. Read the HelmRelease events and the pods it manages in its target namespace.
  - `DependencyNotReady`: an object listed in `dependsOn` is not ready. Follow the chain to the first broken link.
  - Health check timeouts: the deployed workloads do not become ready. Switch to `workload-troubleshooting` for them.
- If many objects fail at once, check the Flux controller pods themselves (usually in the `flux-system` namespace).

## Report

Each broken object with kind, namespace/name, reason, message and since when; the
first broken link in any dependency chain; and the likely fix (for example "fix the
manifest in Git" or "check the repository credentials").
