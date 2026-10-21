"""Tests for one scan of a cluster: inputs, detectors and monitoring access together."""

from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from fakes import by_key, fixture_fetch

from k8s_servant.config import configure_app
from k8s_servant.findings import StoredFinding
from k8s_servant.scan import scan
from k8s_servant.scan.facts import TOPICS
from k8s_servant.scan.inputs import INPUTS

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("flavor", "expected"),
    [
        (
            "aks",
            {
                "platform/cloud",
                "nodepool/system",
                "nodepool/user",
                "component/azure-cni",
                "component/kyverno",
                "component/flux",
                "component/cert-manager",
                "component/ingress-nginx",
                "component/consul",
                "component/external-secrets",
                "component/secrets-store-csi",
                "component/csi-provider-azure",
                "component/prometheus",
                "component/alertmanager",
                "network/default-deny-egress",
                "network/ingress-exposure",
                "monitoring/prometheus-url",
                "noise/alert/KubeCPUOvercommit",
            },
        ),
        (
            "eks",
            {
                "platform/cloud",
                "component/aws-vpc-cni",
                "component/karpenter",
                "component/argo-cd",
                "component/gatekeeper",
                "component/sealed-secrets",
                "component/linkerd",
                "network/no-network-policies",
            },
        ),
        (
            "gke",
            {"platform/cloud", "component/cilium", "component/istio", "component/csi-provider-gcp", "network/gateways"},
        ),
        ("kind", {"platform/cloud", "nodepool/control-plane", "component/kindnet", "component/kyverno"}),
    ],
)
async def test_scan_finds_the_expected_facts(flavor, expected):
    result = await scan(fixture_fetch(flavor), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert result.ok and expected <= {f.key for f in result.facts}


async def test_scan_on_kind_marks_leftover_crds_unsure_and_alerts_not_reachable():
    result = await scan(fixture_fetch("kind"), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert by_key(result.facts, "component/kyverno").certainty == "unsure"
    assert result.failed["prometheus"].startswith("not reachable")
    assert result.failed["alertmanager"].startswith("not reachable")


async def test_scan_prefers_the_configured_prometheus_url():
    calls = []
    transport = httpx2.MockTransport(lambda r: httpx2.Response(200, json={"status": "success", "data": {"result": []}}))
    await scan(
        fixture_fetch("aks", calls),
        configure_app({"PROMETHEUS_URL": "http://p:9090"}),
        "my-cluster",
        clock=lambda: NOW,
        transport=transport,
    )
    assert not any(c[:2] == ["get", "--raw"] and "api/v1/query" in c[2] for c in calls)


async def test_a_scan_with_every_kubectl_input_failing_is_not_ok():
    fail = {name: RuntimeError("Unable to connect") for name, _, _ in INPUTS}
    result = await scan(fixture_fetch("kind", fail=fail), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert not result.ok


async def test_scan_result_carries_cluster_time_failures_and_sorted_facts():
    result = await scan(fixture_fetch("gke"), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert result.cluster == "my-cluster" and result.scanned_at == NOW
    assert result.failed["cilium-policies"] == "RuntimeError: Forbidden"
    order = [(TOPICS.index(f.topic), f.key) for f in result.facts]
    assert order == sorted(order)


async def test_an_unreachable_configured_url_falls_back_to_the_proxy():
    def refuse(request):
        raise httpx2.ConnectError("refused")

    calls = []
    config = configure_app({"PROMETHEUS_URL": "http://p:9090", "ALERTMANAGER_URL": "http://a:9093"})
    result = await scan(
        fixture_fetch("aks", calls), config, "my-cluster", clock=lambda: NOW, transport=httpx2.MockTransport(refuse)
    )
    assert "noise/alert/KubeCPUOvercommit" in {f.key for f in result.facts}
    assert any("/api/v2/alerts" in c[2] for c in calls if c[:2] == ["get", "--raw"])
    assert "prometheus" not in result.failed and "alertmanager" not in result.failed


async def test_the_url_fact_says_checked_only_when_the_proxy_answered():
    result = await scan(fixture_fetch("aks"), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert "checked: it answered" in by_key(result.facts, "monitoring/prometheus-url").setting_reason
    fail = {"raw-prometheus": RuntimeError("ServiceUnavailable")}
    result = await scan(fixture_fetch("aks", fail=fail), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert "not checked" in by_key(result.facts, "monitoring/prometheus-url").setting_reason
    assert result.failed["prometheus"].startswith("not reachable")


async def test_without_prometheus_long_firing_alerts_come_from_alertmanager():
    fail = {"raw-prometheus": RuntimeError("ServiceUnavailable")}
    result = await scan(fixture_fetch("aks", fail=fail), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert by_key(result.facts, "noise/alert/KubeCPUOvercommit").question.startswith(
        "`KubeCPUOvercommit` has been firing since"
    )


class _Memory:
    def __init__(self, findings):
        self.findings = findings
        self.calls = 0

    def open_findings(self):
        self.calls += 1
        return self.findings


async def test_scan_reads_open_findings_from_memory():
    memory = _Memory(
        [StoredFinding("oom/x/Deployment/a", "warning", "a", "", NOW - timedelta(days=5), NOW, "open", None)]
    )
    result = await scan(fixture_fetch("kind"), configure_app({}), "my-cluster", memory=memory, clock=lambda: NOW)
    assert memory.calls == 1
    assert "noise/finding/oom/x/Deployment/a" in {f.key for f in result.facts}


async def test_unreadable_memory_is_a_failure_not_an_exception():
    class Broken:
        def open_findings(self):
            raise OSError("disk I/O error")

    result = await scan(fixture_fetch("kind"), configure_app({}), "my-cluster", memory=Broken(), clock=lambda: NOW)
    assert result.ok and result.failed["memory"] == "OSError: disk I/O error"
