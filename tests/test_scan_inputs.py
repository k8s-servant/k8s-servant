import httpx2
import pytest
from fakes import fixture_fetch

from k8s_servant.scan.inputs import (
    INPUTS,
    alert_shares,
    direct_prometheus,
    gather_inputs,
    proxied_alertmanager,
    proxied_prometheus,
)


async def test_gather_reads_every_input_once():
    calls = []
    inputs = await gather_inputs(fixture_fetch("kind", calls))
    assert inputs.ok("nodes") and inputs.get("version")[0]["serverVersion"]["gitVersion"]
    assert len(calls) == len({tuple(c) for c in calls})


async def test_a_forbidden_input_is_a_failure_not_an_exception():
    inputs = await gather_inputs(fixture_fetch("kind", fail={"networkpolicies": RuntimeError("Forbidden")}))
    assert inputs.failed["networkpolicies"] == "RuntimeError: Forbidden"
    assert inputs.ok("nodes")


async def test_crd_dependent_inputs_are_skipped_when_the_crd_is_absent():
    calls = []
    inputs = await gather_inputs(fixture_fetch("kind", calls))
    assert not any("gateways.gateway.networking.k8s.io" in c for c in calls)
    assert "gateways" not in inputs.failed


async def test_crd_dependent_inputs_fail_when_the_crd_list_failed():
    inputs = await gather_inputs(fixture_fetch("kind", fail={"crds": RuntimeError("Forbidden")}))
    assert inputs.failed["gateways"] == "CRD list not readable"


async def test_no_input_reads_secrets():
    assert not any("secret" in " ".join(args).lower() for _, args, _ in INPUTS)


async def test_alert_shares_through_the_proxy_path():
    calls = []
    query = proxied_prometheus(fixture_fetch("aks", calls), "monitoring", "kps-prometheus", 9090)
    assert await alert_shares(query) == {"KubeCPUOvercommit": 0.96}
    assert calls[0][:2] == ["get", "--raw"] and "/services/kps-prometheus:9090/proxy/api/v1/query?query=" in calls[0][2]


async def test_alert_shares_rejects_an_error_body():
    async def query(_):
        return {"status": "error", "error": "bad"}

    with pytest.raises(ValueError):
        await alert_shares(query)


async def test_direct_prometheus_uses_the_configured_url():
    seen = []
    transport = httpx2.MockTransport(
        lambda r: (seen.append(str(r.url)), httpx2.Response(200, json={"status": "success", "data": {"result": []}}))[1]
    )
    assert await alert_shares(direct_prometheus("http://p:9090", 5, transport)) == {}
    assert seen[0].startswith("http://p:9090/api/v1/query?query=")


async def test_crd_dependent_inputs_are_read_when_the_crd_exists():
    inputs = await gather_inputs(fixture_fetch("gke"))
    assert inputs.ok("gateways") and inputs.get("gateways")


async def test_alertmanager_through_the_proxy_path():
    calls = []
    alerts = proxied_alertmanager(fixture_fetch("aks", calls), "monitoring", "kps-alertmanager", 9093)
    found = await alerts()
    assert [a["labels"]["alertname"] for a in found] == ["KubeCPUOvercommit"]
    assert calls[0] == [
        "get",
        "--raw",
        "/api/v1/namespaces/monitoring/services/kps-alertmanager:9093/proxy"
        "/api/v2/alerts?active=true&silenced=false&inhibited=false",
    ]
