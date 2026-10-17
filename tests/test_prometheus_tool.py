import httpx2

from k8s_servant.tools.prometheus_tool import EMPTY_RESULT, build_prometheus_tools, check_ready, format_alerts

T = 1760248800  # 2025-10-12T06:00:00Z


def _tools(handler, prometheus="http://prom:9090", alertmanager="http://am:9093"):
    transport = httpx2.MockTransport(handler)
    return {tool.name: tool for tool in build_prometheus_tools(prometheus, alertmanager, 5, transport=transport)}


def _ok(result_type, result):
    return httpx2.Response(200, json={"status": "success", "data": {"resultType": result_type, "result": result}})


async def test_instant_query_lists_series():
    seen = []

    def handler(request):
        seen.append(request)
        return _ok("vector", [{"metric": {"__name__": "up", "job": "kubelet"}, "value": [T, "1"]}])

    tools = _tools(handler)
    assert await tools["prometheus_query"].run({"query": "up"}) == 'up{job="kubelet"} 1'
    assert seen[0].url.path == "/api/v1/query" and seen[0].url.params["query"] == "up"
    assert all(tool.surface == "prometheus" for tool in tools.values())


async def test_range_query_is_summarised():
    def handler(request):
        assert request.url.params["step"] == "1m"
        return _ok("matrix", [{"metric": {"pod": "api-1"}, "values": [[T, "1"], [T + 60, "5"], [T + 120, "3"]]}])

    out = await _tools(handler)["prometheus_query_range"].run(
        {"query": "x", "start": "2025-10-12T06:00:00Z", "end": "2025-10-12T06:02:00Z", "step": "1m"}
    )
    assert out == '{pod="api-1"} points=3 first=1 last=3 min=1 max=5 from=2025-10-12T06:00:00Z to=2025-10-12T06:02:00Z'


async def test_empty_result_says_it_is_not_proof():
    assert await _tools(lambda r: _ok("vector", []))["prometheus_query"].run({"query": "up"}) == EMPTY_RESULT


async def test_prometheus_errors_are_reported():
    def handler(request):
        return httpx2.Response(400, json={"status": "error", "errorType": "bad_data", "error": "parse error"})

    assert await _tools(handler)["prometheus_query"].run({"query": "up{"}) == "ERROR: bad_data: parse error"


async def test_missing_arguments():
    tools = _tools(lambda r: _ok("vector", []))
    assert (await tools["prometheus_query"].run({})).startswith("ERROR:")
    assert await tools["prometheus_query_range"].run({"query": "x", "start": "a", "end": "b"}) == "ERROR: missing step"


async def test_alerts_are_firing_only_and_sorted_by_severity():
    def handler(request):
        params = request.url.params
        assert request.url.path == "/api/v2/alerts"
        assert (params["active"], params["silenced"], params["inhibited"]) == ("true", "false", "false")
        assert params.get_list("filter") == ['namespace="kube-system"']
        return httpx2.Response(
            200,
            json=[
                {
                    "labels": {"alertname": "CPUThrottlingHigh", "severity": "info", "namespace": "kube-system"},
                    "annotations": {"summary": "throttled"},
                    "startsAt": "2026-10-12T05:00:00Z",
                },
                {
                    "labels": {
                        "alertname": "KubePodCrashLooping",
                        "severity": "critical",
                        "namespace": "kube-system",
                        "pod": "x",
                    },
                    "annotations": {"description": "crash"},
                    "startsAt": "2026-10-12T04:00:00Z",
                },
            ],
        )

    out = await _tools(handler)["alertmanager_alerts"].run({"filter": ['namespace="kube-system"']})
    lines = out.splitlines()
    assert lines[0].startswith("KubePodCrashLooping [critical] namespace=kube-system pod=x")
    assert lines[1].startswith("CPUThrottlingHigh [info]")


def test_format_alerts_when_none_fire():
    assert format_alerts([]) == "(no firing alerts)"


def test_tools_exist_only_for_configured_urls():
    assert build_prometheus_tools("", "", 5) == []
    assert [t.name for t in build_prometheus_tools("http://p", "", 5)] == ["prometheus_query", "prometheus_query_range"]
    assert [t.name for t in build_prometheus_tools("", "http://a", 5)] == ["alertmanager_alerts"]


async def test_check_ready():
    assert await check_ready("http://p", 5, transport=httpx2.MockTransport(lambda r: httpx2.Response(200))) is None
    assert (
        await check_ready("http://p", 5, transport=httpx2.MockTransport(lambda r: httpx2.Response(503))) == "HTTP 503"
    )


async def test_alerts_fired_since_reads_the_alerts_history():
    from k8s_servant.tools.prometheus_tool import fetch_alerts_fired_since

    seen = []

    def handler(request):
        seen.append(request)
        return _ok(
            "vector",
            [{"metric": {"alertname": "KubeNodeNotReady", "severity": "warning", "node": "n1"}, "value": [T, "1"]}],
        )

    alerts = await fetch_alerts_fired_since("http://prom:9090", 36000, 5, transport=httpx2.MockTransport(handler))
    assert alerts == [{"alertname": "KubeNodeNotReady", "severity": "warning", "node": "n1"}]
    assert 'ALERTS{alertstate="firing"}[36000s]' in seen[0].url.params["query"]
