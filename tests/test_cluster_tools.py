"""Tests for the compact cluster summary tools (cluster_problems, warning_events).

Kubernetes objects are written by hand as the minimal JSON the checks read. The
fetch function is faked, so no cluster is needed.
"""

from datetime import UTC, datetime, timedelta

import pytest

from k8s_servant.tools.cluster_tools import (
    build_cluster_tools,
    cluster_problems,
    controller_problems,
    job_problems,
    node_problems,
    parse_quantity,
    pdb_problems,
    pod_problems,
    pvc_problems,
    quota_problems,
    warning_events,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- quantities ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text, value",
    [
        ("2", 2),
        ("500m", 0.5),
        ("1200m", 1.2),
        ("4Gi", 4 * 1024**3),
        ("640Mi", 640 * 1024**2),
        ("1k", 1000),
        ("1M", 1e6),
        ("1.5", 1.5),
        ("1e3", 1000),
    ],
)
def test_parse_quantity(text, value):
    assert parse_quantity(text) == pytest.approx(value)


# --- nodes --------------------------------------------------------------------


def _node(name, ready="True", unschedulable=False, **conditions):
    conds = [{"type": "Ready", "status": ready, "lastTransitionTime": _ts(30), "reason": "KubeletNotReady"}]
    conds += [{"type": t, "status": s} for t, s in conditions.items()]
    return {"metadata": {"name": name}, "spec": {"unschedulable": unschedulable}, "status": {"conditions": conds}}


def test_healthy_node_is_not_reported():
    assert node_problems([_node("n1", MemoryPressure="False")]) == []


def test_node_problems():
    lines = node_problems(
        [
            _node("n1", ready="False"),
            _node("n2", ready="Unknown"),
            _node("n3", MemoryPressure="True"),
            _node("n4", unschedulable=True),
        ]
    )
    text = "\n".join(lines)
    assert "Node n1: NotReady" in text and "Node n2: NotReady" in text
    assert "Node n3: MemoryPressure" in text
    assert "Node n4: cordoned" in text


# --- pods ---------------------------------------------------------------------


def _pod(name, ns="shop", phase="Running", owner=("ReplicaSet", "api-5474575d8c"), containers=(), **extra):
    pod = {
        "metadata": {"name": name, "namespace": ns, "creationTimestamp": _ts(600)},
        "status": {"phase": phase, "containerStatuses": list(containers)},
    }
    if owner:
        pod["metadata"]["ownerReferences"] = [{"kind": owner[0], "name": owner[1]}]
    pod["status"].update(extra)
    return pod


def _container(name="app", ready=True, restarts=0, waiting=None, last_reason=None, last_finished=10):
    status = {"name": name, "ready": ready, "restartCount": restarts, "state": {"running": {}}}
    if waiting:
        status["state"] = {"waiting": {"reason": waiting}}
    if last_reason:
        status["lastState"] = {"terminated": {"reason": last_reason, "exitCode": 137, "finishedAt": _ts(last_finished)}}
    return status


def test_healthy_and_finished_pods_are_not_reported():
    pods = [
        _pod("api-5474575d8c-a", containers=[_container()]),
        _pod("job-x", phase="Succeeded", owner=("Job", "job")),
        # Old restarts (last one 3 days ago) are history, not a current problem.
        _pod("api-5474575d8c-b", containers=[_container(restarts=12, last_reason="Error", last_finished=3 * 1440)]),
    ]
    assert pod_problems(pods, now=NOW) == []


def test_unhealthy_pods_are_grouped_by_owning_workload():
    pods = [
        _pod("api-5474575d8c-a", containers=[_container(ready=False, restarts=7, waiting="CrashLoopBackOff")]),
        _pod("api-5474575d8c-b", containers=[_container(ready=False, restarts=6, waiting="CrashLoopBackOff")]),
        _pod(
            "web-0",
            owner=("StatefulSet", "web"),
            phase="Pending",
            conditions=[
                {
                    "type": "PodScheduled",
                    "status": "False",
                    "reason": "Unschedulable",
                    "message": "0/3 nodes are available: insufficient memory.",
                }
            ],
        ),
        _pod("worker-x", owner=None, phase="Failed", reason="Evicted"),
        _pod(
            "cache-6d4f8-z",
            containers=[_container(restarts=8, last_reason="OOMKilled", last_finished=20)],
            owner=("ReplicaSet", "cache-6d4f8"),
        ),
    ]
    lines = pod_problems(pods, now=NOW)
    text = "\n".join(lines)
    assert len(lines) == 4
    assert "Deployment shop/api: 2 of its pods unhealthy: CrashLoopBackOff" in text
    assert "e.g. pod api-5474575d8c-a" in text
    assert "StatefulSet shop/web: 1 of its pods unhealthy: Pending (Unschedulable: 0/3 nodes are available" in text
    assert "Pod shop/worker-x: 1 of its pods unhealthy: Failed (Evicted)" in text
    assert "Deployment shop/cache: 1 of its pods unhealthy: 8 restarts, last OOMKilled" in text


def test_terminating_pods_are_ignored():
    pod = _pod("api-5474575d8c-a", containers=[_container(ready=False)])
    pod["metadata"]["deletionTimestamp"] = _ts(1)
    assert pod_problems([pod], now=NOW) == []


# --- controllers --------------------------------------------------------------


def test_controller_problems():
    deployments = [
        {"metadata": {"namespace": "shop", "name": "ok"}, "spec": {"replicas": 2}, "status": {"availableReplicas": 2}},
        {"metadata": {"namespace": "shop", "name": "scaled-down"}, "spec": {"replicas": 0}, "status": {}},
        {
            "metadata": {"namespace": "shop", "name": "api"},
            "spec": {"replicas": 3},
            "status": {
                "availableReplicas": 1,
                "conditions": [
                    {
                        "type": "ReplicaFailure",
                        "status": "True",
                        "reason": "FailedCreate",
                        "message": 'pods "api-x" is forbidden: exceeded quota: default-ns-compute',
                    }
                ],
            },
        },
    ]
    statefulsets = [
        {"metadata": {"namespace": "db", "name": "pg"}, "spec": {"replicas": 2}, "status": {"readyReplicas": 1}}
    ]
    daemonsets = [
        {
            "metadata": {"namespace": "kube-system", "name": "cni"},
            "status": {"desiredNumberScheduled": 5, "numberReady": 4},
        }
    ]
    text = "\n".join(controller_problems(deployments, statefulsets, daemonsets))
    assert "ok" not in text and "scaled-down" not in text
    assert (
        'Deployment shop/api: 1/3 available; ReplicaFailure FailedCreate: pods "api-x" is forbidden: exceeded quota'
        in text
    )
    assert "StatefulSet db/pg: 1/2 ready" in text
    assert "DaemonSet kube-system/cni: 4/5 ready" in text


# --- PDBs, quotas, PVCs, jobs ---------------------------------------------------


def test_pdb_problems():
    pdbs = [
        {
            "metadata": {"namespace": "a", "name": "fine"},
            "status": {"expectedPods": 2, "currentHealthy": 2, "desiredHealthy": 1, "disruptionsAllowed": 1},
        },
        {
            "metadata": {"namespace": "a", "name": "single-replica"},  # blocks drains, but is healthy: not a finding
            "status": {"expectedPods": 1, "currentHealthy": 1, "desiredHealthy": 1, "disruptionsAllowed": 0},
        },
        {
            "metadata": {"namespace": "quick-dev", "name": "orphan"},
            "status": {"expectedPods": 0, "currentHealthy": 0, "desiredHealthy": 1, "disruptionsAllowed": 0},
        },
        {
            "metadata": {"namespace": "b", "name": "short"},
            "status": {"expectedPods": 3, "currentHealthy": 1, "desiredHealthy": 2, "disruptionsAllowed": 0},
        },
    ]
    text = "\n".join(pdb_problems(pdbs))
    assert "fine" not in text and "single-replica" not in text
    assert "PodDisruptionBudget quick-dev/orphan: matches no pods" in text
    assert "PodDisruptionBudget b/short: 1 healthy, needs 2" in text


def test_quota_problems():
    quotas = [
        {
            "metadata": {"namespace": "shop-dev", "name": "default-ns-compute"},
            "status": {
                "hard": {
                    "limits.memory": "4Gi",
                    "limits.cpu": "4",
                    "requests.memory": "2Gi",
                    "services.loadbalancers": "0",
                },
                "used": {
                    "limits.memory": "4Gi",
                    "limits.cpu": "1200m",
                    "requests.memory": "1900Mi",
                    "services.loadbalancers": "0",
                },
            },
        }
    ]
    text = "\n".join(quota_problems(quotas))
    assert "ResourceQuota shop-dev/default-ns-compute: limits.memory 4Gi of 4Gi used (100%)" in text
    assert "requests.memory 1900Mi of 2Gi used (93%)" in text
    assert "limits.cpu" not in text and "loadbalancers" not in text


def test_pvc_and_job_problems():
    pvcs = [
        {"metadata": {"namespace": "db", "name": "data"}, "status": {"phase": "Pending"}},
        {"metadata": {"namespace": "db", "name": "ok"}, "status": {"phase": "Bound"}},
    ]
    assert pvc_problems(pvcs) == ["- PersistentVolumeClaim db/data: Pending"]
    jobs = [
        {
            "metadata": {
                "namespace": "ops",
                "name": "backup-29312",
                "ownerReferences": [{"kind": "CronJob", "name": "backup"}],
            },
            "status": {
                "conditions": [
                    {
                        "type": "Failed",
                        "status": "True",
                        "reason": "BackoffLimitExceeded",
                        "message": "Job has reached the specified backoff limit",
                        "lastTransitionTime": _ts(90),
                    }
                ]
            },
        },
        {
            "metadata": {"namespace": "ops", "name": "old"},
            "status": {
                "conditions": [
                    {
                        "type": "Failed",
                        "status": "True",
                        "reason": "BackoffLimitExceeded",
                        "lastTransitionTime": _ts(3 * 1440),
                    }
                ]
            },
        },
    ]
    text = "\n".join(job_problems(jobs, now=NOW))
    assert "Job ops/backup-29312 (CronJob backup): failed 2026-10-12 10:30 UTC: BackoffLimitExceeded" in text
    assert "old" not in text


# --- cluster_problems (the tool) ------------------------------------------------


def _fake_fetch(objects: dict[str, list], calls: list, fail: tuple = ()):
    async def fetch(args):
        calls.append(args)
        resource = args[1]
        if resource in fail:
            raise RuntimeError("forbidden")
        return {"items": objects.get(resource, [])}

    return fetch


async def test_cluster_problems_reports_coverage_and_problems():
    calls = []
    objects = {
        "nodes": [_node("n1")],
        "pods": [_pod("api-5474575d8c-a", containers=[_container(ready=False, waiting="CrashLoopBackOff")])],
        "poddisruptionbudgets": [
            {
                "metadata": {"namespace": "q", "name": "orphan"},
                "status": {"expectedPods": 0, "currentHealthy": 0, "desiredHealthy": 1},
            }
        ],
    }
    text = await cluster_problems(_fake_fetch(objects, calls, fail=("jobs",)), now=NOW)
    assert text.startswith("Cluster problem sweep, all namespaces, 2026-10-12 12:00 UTC")
    assert "Checked: 1 nodes, 1 pods, 0 deployments" in text
    assert "Problems found: 2" in text
    assert "Deployment shop/api" in text and "PodDisruptionBudget q/orphan" in text
    assert "Could not check jobs: RuntimeError: forbidden" in text
    assert ["get", "pods", "-A", "-o", "json"] in calls


async def test_cluster_problems_for_one_namespace_skips_nodes():
    calls = []
    text = await cluster_problems(_fake_fetch({}, calls), namespace="shop", now=NOW)
    assert "namespace shop" in text and "Problems found: 0" in text
    assert all(call[1] != "nodes" for call in calls)
    assert ["get", "pods", "-n", "shop", "-o", "json"] in calls


async def test_long_sections_are_capped():
    pvcs = [{"metadata": {"namespace": "ns", "name": f"pvc-{i}"}, "status": {"phase": "Pending"}} for i in range(70)]
    text = await cluster_problems(_fake_fetch({"persistentvolumeclaims": pvcs}, []), now=NOW, max_lines=50)
    assert "pvc-49" in text and "pvc-50" not in text
    assert "... 20 more" in text


# --- warning_events -------------------------------------------------------------


def _event(ns, kind, name, reason, minutes_ago, count=1, message="msg"):
    return {
        "metadata": {"namespace": ns},
        "involvedObject": {"kind": kind, "name": name},
        "reason": reason,
        "type": "Warning",
        "count": count,
        "lastTimestamp": _ts(minutes_ago),
        "message": message,
    }


async def test_warning_events_are_grouped_and_policy_noise_is_excluded_server_side():
    calls = []
    events = [
        _event("shop-dev", "ReplicaSet", "shop-dev-5474575d8c", "FailedCreate", 5, message="exceeded quota: a"),
        _event(
            "shop-dev", "ReplicaSet", "shop-dev-5474575d8c", "FailedCreate", 3, count=2, message="exceeded quota: b"
        ),
        _event("shop", "Pod", "api-x", "Unhealthy", 10, message="Readiness probe failed:\nconnection refused"),
        _event("shop", "Pod", "old", "BackOff", 300),  # older than the window
    ]
    text = await warning_events(_fake_fetch({"events": events}, calls), exclude_reasons=("PolicyViolation",), now=NOW)
    assert calls == [["get", "events", "-A", "--field-selector", "type=Warning,reason!=PolicyViolation", "-o", "json"]]
    assert "4 Warning events in 2 groups, last 60 min, all namespaces" in text
    assert "Hidden reasons: PolicyViolation" in text
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert lines[0].startswith(
        "- shop-dev ReplicaSet/shop-dev-5474575d8c FailedCreate x3, last 11:57 UTC: exceeded quota: b"
    )
    assert "Readiness probe failed: connection refused" in lines[1]
    assert "old" not in text


async def test_warning_events_can_show_one_reason_or_everything():
    calls = []
    fetch = _fake_fetch({"events": []}, calls)
    await warning_events(
        fetch, namespace="shop", reason="PolicyViolation", exclude_reasons=("PolicyViolation",), now=NOW
    )
    await warning_events(fetch, include_excluded=True, exclude_reasons=("PolicyViolation",), now=NOW)
    assert calls[0] == [
        "get",
        "events",
        "-n",
        "shop",
        "--field-selector",
        "type=Warning,reason=PolicyViolation",
        "-o",
        "json",
    ]
    assert calls[1][3:5] == ["--field-selector", "type=Warning"]


# --- the Tool wrappers: argument validation -------------------------------------


@pytest.mark.parametrize(
    "arguments",
    [
        {"namespace": "-o=go-template-file=/var/run/secrets/token"},
        {"namespace": "Shop"},
        {"namespace": "a" * 64},
        {"reason": "x,type!=Warning"},
        {"since_minutes": 0},
        {"since_minutes": "lots"},
    ],
)
async def test_tool_arguments_are_validated(arguments):
    tools = {t.name: t for t in build_cluster_tools("kubectl", 5, ("PolicyViolation",))}
    text = await tools["warning_events"].run(arguments)
    assert text.startswith("ERROR: ")


async def test_tools_describe_the_excluded_reasons():
    tools = {t.name: t for t in build_cluster_tools("kubectl", 5, ("PolicyViolation", "Other"))}
    assert set(tools) == {"cluster_problems", "warning_events"}
    assert "PolicyViolation, Other" in tools["warning_events"].description
    assert tools["cluster_problems"].surface == "cluster"
    text = await tools["cluster_problems"].run({"namespace": "--kubeconfig=/tmp/x"})
    assert text.startswith("ERROR: ")
