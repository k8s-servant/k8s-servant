---
name: cluster-health-check
description: Use for a scheduled or on-demand overall health sweep of the cluster - nodes, unhealthy pods and workloads, disruption budgets, quotas, recent warning events, storage, Flux GitOps objects and firing alerts. Produces a prioritised list of findings with evidence.
---
# Cluster health check

A breadth-first sweep: find what is wrong across the whole cluster quickly, then go
one level deeper on each thing that looks wrong. Do not run every deep-dive procedure
on every object.

## Rules

- Read-only. You never change the cluster; when something needs fixing, name the fix for the administrators.
- Start with the compact summary tools (`cluster_problems`, `warning_events`). Do not list every pod, deployment or event of a large cluster yourself: the output gets cut and problems are missed.
- For the follow-up on one object prefer the Kubernetes MCP tools; use kubectl when no MCP tool fits.
- Before flagging something, check the environment description and cluster notes: they say what is normal here (maintenance windows, expected restarts, components that are deliberately absent).
- Every finding needs evidence: the object, the observed state and when it was observed.
- Work through all steps on your own. Do not stop to ask whether to continue.

## Steps

### 1. Sweep

- Call `cluster_problems` (all namespaces). It returns only what is unhealthy:
  nodes, pods grouped by owning workload, Deployments / StatefulSets / DaemonSets
  short of ready pods, PodDisruptionBudgets, ResourceQuotas at or above 90%, PVCs
  that are not Bound and Jobs that failed in the last 24 h, plus how many objects it
  checked. If it reports "Could not check ...", cover that part with the steps below.
- Call `warning_events` (last 60 minutes). Some reasons are hidden by default
  (policy-scan noise); that is expected.
- If these tools are not available, use the manual steps 2-6 with narrow queries.

### 2. Nodes

- Flag: `NotReady` or `Unknown`; cordoned for longer than maintenance would take; `MemoryPressure`, `DiskPressure` or `PIDPressure`; mixed kubelet versions that suggest a stuck upgrade.
- Get node resource usage and flag nodes above ~90% CPU or memory.

### 3. Workloads

- For each unhealthy workload from the sweep, take one pod and look one level deeper: describe it and read its events; for crashes read the last log lines of the previous container. Load `workload-troubleshooting` when the cause is not obvious.
- Typical states: Pending, CrashLoopBackOff, ImagePullBackOff / ErrImagePull, OOMKilled, CreateContainerConfigError, Evicted, many restarts.
- One finding per owning workload, not per pod.
- A system component restarting (DNS, CNI, kube-proxy, ingress or gateway, admission webhooks) matters more than an application doing so.

### 4. Disruption budgets and quotas

- A PodDisruptionBudget that matches no pods protects nothing and keeps a "not enough healthy pods" alert firing. Usually its workload was removed or scaled to zero; the fix is to delete the budget or restore the workload. Severity warning.
- A PodDisruptionBudget with fewer healthy pods than it needs blocks node drains and upgrades. Severity warning.
- A ResourceQuota at 100% of a limit makes the API server refuse new pods (`FailedCreate ... exceeded quota` events), which also breaks rolling updates that need an extra pod. Severity warning while the workload still runs, critical when it is down.

### 5. Warning events

- Look for patterns: FailedScheduling, FailedCreate, FailedMount / FailedAttachVolume, BackOff, Unhealthy (probe failures), FailedCreatePodSandBox, NodeNotReady, Evicted, OOMKilling.
- Repeated events (high count) matter more than one-offs. Tie events to the workload findings from step 3 instead of reporting them separately.

### 6. Storage

- PersistentVolumeClaims that are not Bound (Pending or Lost), and attach or mount failures in the events.

### 7. GitOps

- If the Flux custom resources exist in the cluster, load `flux-gitops-status` and run its quick check.

### 8. Alerts

- If an Alertmanager or Prometheus tool is available, list the firing alerts.
- Every firing alert must end up in the report: as its own finding (category alert-firing), as evidence of another finding, or explained as expected with the reason (the environment description says so, or you verified it is a false alarm). Always-on alerts that only prove the alerting pipeline works (for example Watchdog, InfoInhibitor) can be skipped.
- Use the alert's severity label as a starting point for the finding's severity, and the labels (namespace, pod, deployment, poddisruptionbudget, ...) to find the object.

## Severity

- **critical**: user-facing impact likely or capacity at risk: a node NotReady, a system component down (DNS, CNI, ingress or gateway, admission webhooks), a workload with zero ready replicas, a PVC Lost.
- **warning**: degraded but working: replicas missing, repeated restarts, pressure conditions, Flux objects not Ready, Pending pods, firing warning alerts, budgets or quotas that block pods or drains.
- **info**: worth knowing, no action needed soon.

## Report

Start with one line on overall health. Then the findings, most severe first, each
with what is wrong, the evidence, the likely cause and the suggested fix. End with
what you checked and found healthy (the coverage line of `cluster_problems` helps),
so the reader knows the coverage.
