---
name: node-troubleshooting
description: Use when a node is NotReady, cordoned, under memory, disk or PID pressure, out of allocatable resources, or when many pods fail on the same node. Also covers cluster autoscaler behaviour (scale-up not happening, pods waiting for capacity).
---
# Node troubleshooting

## Rules

- Read-only: name the fix, never apply it (no cordon, drain or reboot).
- Check the environment description for planned maintenance (OS patch reboots, upgrade windows) before calling a NotReady node an incident.

## Steps

### 1. Node state

- Get nodes with wide output: status, version, age, internal IP, OS image, kernel.
- Describe the affected node: Conditions (Ready, MemoryPressure, DiskPressure, PIDPressure, NetworkUnavailable) with their lastTransitionTime and message; Taints; Unschedulable; Allocatable versus allocated resources; Events.
- `Ready=Unknown` means the kubelet stopped reporting (node down, network, kubelet crash). `Ready=False` means the kubelet reports a problem; read the message.

### 2. Timing

- When did the condition change? Compare with the node's age (a new node may still be joining) and with maintenance windows from the environment description.
- Several nodes changing at the same time point at something shared: an upgrade, the network, a zone.

### 3. Pressure and capacity

- Get live node CPU and memory usage.
- MemoryPressure and DiskPressure lead to evictions: look for Evicted pods and eviction events on the node.
- Allocated requests close to allocatable cause Pending pods even when real usage is low. Report both numbers.

### 4. Pods on the node

- List the pods on the node (field selector `spec.nodeName=<node>`), especially system DaemonSets (network plugin, kube-proxy, storage drivers, log and metric collectors). A failing DaemonSet pod on one node is usually the node's problem, not the workload's.

### 5. Autoscaler

- If the cluster autoscales, read the events of Pending pods: `TriggeredScaleUp` (scale-up in progress) or `NotTriggerScaleUp` (the message says why, e.g. the node group is at its maximum size).
- A node group at its maximum size with Pending pods is a capacity finding.

## Report

The affected node(s), the condition and since when, the likely cause, the impact
(which pods are affected), whether it looks like planned maintenance, and the
suggested action.
