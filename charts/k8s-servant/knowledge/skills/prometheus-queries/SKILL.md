---
name: prometheus-queries
description: Use when metrics can confirm or time a problem - memory or CPU close to limits, CPU throttling, restart trends, node resource pressure, disk or PVC filling up, API server or DNS errors - or when you need to know when something started or whether it is getting worse. Needs the prometheus_query tools.
---
# Prometheus queries

Metrics add what the Kubernetes API cannot show: how close something is to a
limit, and how values change over time.

## Rules

- Read-only. Queries never change anything.
- Times are in UTC. Say so when you report a time.
- For "when did it start" or "is it getting worse" use a range query, not an instant one.
- Compare with a baseline: the same window a day or a week earlier (`offset 1d`, `offset 7d`).
- An empty result is not proof that all is well: the metric may be named differently or not collected. Check with a simpler query (e.g. `count(<metric>)`) before concluding.
- Keep results small: aggregate with `sum by (...)`, `topk(10, ...)`, and filter with label matchers.

## Proven queries

**Restarts**
- Restarts in the last hour, per workload: `sum by (namespace, pod) (increase(kube_pod_container_status_restarts_total[1h])) > 0`
- Containers terminated by out-of-memory: `kube_pod_container_status_last_terminated_reason{reason="OOMKilled"} == 1`

**Memory**
- Working set as a share of the limit (above 0.9 is a risk of OOM kill):
  `max by (namespace, pod, container) (container_memory_working_set_bytes{container!=""}) / max by (namespace, pod, container) (kube_pod_container_resource_limits{resource="memory"}) > 0.9`

**CPU**
- Throttling ratio (above 0.25 means the CPU limit slows the container down):
  `sum by (namespace, pod, container) (increase(container_cpu_cfs_throttled_periods_total[15m])) / sum by (namespace, pod, container) (increase(container_cpu_cfs_periods_total[15m])) > 0.25`
- CPU use compared with requests: `sum by (namespace, pod) (rate(container_cpu_usage_seconds_total{container!=""}[5m])) / sum by (namespace, pod) (kube_pod_container_resource_requests{resource="cpu"})`

**Nodes**
- Memory available: `node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes`
- CPU busy: `1 - avg by (instance) (rate(node_cpu_seconds_total{mode="idle"}[5m]))`
- Root filesystem free: `node_filesystem_avail_bytes{mountpoint="/"} / node_filesystem_size_bytes{mountpoint="/"}`

**Workloads**
- Deployments missing replicas: `kube_deployment_status_replicas_unavailable > 0`
- Pods not running: `sum by (namespace, phase) (kube_pod_status_phase{phase=~"Pending|Failed|Unknown"}) > 0`

**Storage**
- Volumes more than 85% full: `kubelet_volume_stats_used_bytes / kubelet_volume_stats_capacity_bytes > 0.85`

**Control plane and DNS**
- API server error rate: `sum(rate(apiserver_request_total{code=~"5.."}[5m])) / sum(rate(apiserver_request_total[5m]))`
- CoreDNS errors: `sum by (rcode) (rate(coredns_dns_responses_total{rcode=~"SERVFAIL|REFUSED"}[5m]))`

## Report

Give the query you ran, the numbers that matter (with units and the UTC time window),
how they compare with the baseline, and what you conclude.
