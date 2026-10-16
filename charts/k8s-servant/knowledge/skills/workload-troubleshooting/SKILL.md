---
name: workload-troubleshooting
description: Use when a specific pod or workload is unhealthy - Pending, CrashLoopBackOff, OOMKilled, ImagePullBackOff, failing readiness or liveness probes, or a Deployment that does not reach its desired replicas. Finds the root cause from pod status, events, logs and the owning controller.
---
# Workload troubleshooting

## Rules

- Read-only: name the fix, never apply it.
- Start from the symptom you were given. Establish the namespace, the workload and the time window first; without a time window assume "now".
- Controllers (and a descheduler, if the environment description mentions one) may delete and recreate pods. That resets restart counts and removes previous logs, so absence of evidence is not evidence of health.

## Steps

### 1. Find the owner and the scope

- Get the pods with wide output: status, restarts, age, node.
- Follow ownerReferences: Pod -> ReplicaSet -> Deployment, or StatefulSet, DaemonSet, Job.
- One pod or all replicas? One node or many? All replicas failing points at the image, configuration or a dependency; one pod or one node points at the node or scheduling.

### 2. Read the pod

- Describe the pod. Look at each container's `State` and `Last State` (reason, exit code, finished time), `Ready`, the probe definitions, resource requests and limits, volumes, and the Events at the end.

### 3. Branch by symptom

**Pending**

- The scheduler's reason is in the events (FailedScheduling):
  - `Insufficient cpu` / `Insufficient memory`: requests do not fit free capacity. Compare node allocatable with allocated requests. A cluster autoscaler may be adding a node (TriggeredScaleUp) or refusing to (NotTriggerScaleUp, with the reason).
  - `untolerated taint`, `didn't match node affinity/selector`: placement rules.
  - `unbound immediate PersistentVolumeClaims`, `volume node affinity conflict`: storage; zone mismatch is common with zonal disks.
- `exceeded quota` appears on the ReplicaSet's events, not the pod's: the namespace ResourceQuota is full. Compare `used` with `hard`.
- No pod at all: read the Deployment and ReplicaSet events; the pod may never have been created.

**CrashLoopBackOff / Error**

- Exit code from `Last State`: 1 = application error (read the logs), 137 = killed (out of memory or failed liveness probe), 143 = SIGTERM, 126/127 = command not executable / not found.
- Read the previous container's logs (`--previous`, `--tail=100`). If they are gone, read the current logs and the events.
- A liveness probe killing the container shows as `Unhealthy` events followed by `Killing`.
- Look for a recent change: Deployment revision, image tag, referenced ConfigMaps.

**OOMKilled**

- Confirm the reason OOMKilled and the memory limit. If the pod runs now, compare with its actual usage.
- Suggest raising the limit or investigating a leak; never invent numbers without evidence.

**ImagePullBackOff / ErrImagePull**

- The event message says why: not found (wrong repository or tag), unauthorized (pull secret or registry permission), timeout (network or egress).

**Running but not Ready**

- Readiness probe failures in the events; read the logs around the failure time; check that the probe path and port match the container.
- For a Service backend, check that the Service selector matches the pod labels and that its endpoints are populated.

**Deployment not progressing**

- Deployment conditions (`Progressing` false, `ProgressDeadlineExceeded`), ReplicaSet events, and new versus old ReplicaSet replica counts.

### 4. Correlate

- Events on the node the pod runs on, and other pods on that node.
- Whether the same problem appears in other replicas or other namespaces.

## Report

The root cause (or the most likely one, with your confidence), the evidence (object,
field, event, log line), the suggested fix, and what is still unknown.
