"""Read-only Prometheus and Alertmanager tools.

Metrics answer questions the Kubernetes API cannot: "how close is this container
to its memory limit?", "when did the restarts start?", "is the API server
returning errors?". Firing alerts are strong evidence as well.

  prometheus_query        instant PromQL query       GET /api/v1/query
  prometheus_query_range  PromQL over a time range   GET /api/v1/query_range
  alertmanager_alerts     alerts firing right now    GET /api/v2/alerts

All requests are GETs, so these tools can only read. Each tool is registered only
when its URL is configured (PROMETHEUS_URL, ALERTMANAGER_URL). Range results are
summarised per series (first/last/min/max with timestamps) because thousands of
raw points would only fill the model's context.
"""

import json
import logging
from datetime import UTC, datetime

import httpx2

from k8s_servant.tools.registry import Tool

logger = logging.getLogger(__name__)

EMPTY_RESULT = (
    "(empty result: no series matched. That is not proof that all is well - check the metric name and labels.)"
)

_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _labels(metric: dict) -> str:
    """`name{label="value",...}` like Prometheus prints it."""
    name = metric.get("__name__", "")
    labels = ",".join(f'{key}="{value}"' for key, value in sorted(metric.items()) if key != "__name__")
    return f"{name}{{{labels}}}"


def _iso(timestamp) -> str:
    """Unix seconds as an RFC 3339 UTC timestamp."""
    return datetime.fromtimestamp(float(timestamp), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _number(value) -> str:
    """A sample value in its shortest form (`1`, `0.25`, `1.5e+09`); non-numbers as they are."""
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return str(value)


def format_query_result(data: dict) -> str:
    """Turn the `data` part of a Prometheus response into compact text."""
    result_type, result = data.get("resultType"), data.get("result")
    if result_type == "vector":
        if not result:
            return EMPTY_RESULT
        return "\n".join(f"{_labels(series.get('metric', {}))} {_number(series['value'][1])}" for series in result)
    if result_type == "matrix":
        lines = []
        for series in result or []:
            points = series.get("values") or []
            if not points:
                continue
            values = [float(value) for _, value in points]
            lines.append(
                f"{_labels(series.get('metric', {}))} points={len(values)} first={values[0]:g} last={values[-1]:g} "
                f"min={min(values):g} max={max(values):g} from={_iso(points[0][0])} to={_iso(points[-1][0])}"
            )
        return "\n".join(lines) or EMPTY_RESULT
    if result_type in ("scalar", "string") and isinstance(result, list) and len(result) == 2:
        return f"{_number(result[1])} (at {_iso(result[0])})"
    return json.dumps(data)


async def _prometheus_get(base_url: str, path: str, params: dict, timeout: float, transport) -> str:
    """GET a Prometheus API path and return formatted text or 'ERROR: ...'."""
    async with httpx2.AsyncClient(timeout=timeout, transport=transport) as client:
        response = await client.get(f"{base_url}{path}", params=params)
    try:
        body = response.json()
    except ValueError:
        return f"ERROR: HTTP {response.status_code} from Prometheus: {response.text[:300]}"
    if body.get("status") != "success":
        return f"ERROR: {body.get('errorType', f'HTTP {response.status_code}')}: {body.get('error', '')}"
    return format_query_result(body.get("data") or {})


async def fetch_firing_alerts(
    alertmanager_url: str, timeout: float, filters: list[str] | None = None, transport=None
) -> list[dict]:
    """Alerts that are firing now (active, not silenced, not inhibited).

    Args:
        alertmanager_url: Alertmanager base URL.
        timeout: Seconds for the request.
        filters: Label matchers, e.g. ['namespace="kube-system"'].
        transport: httpx2 transport; tests pass a MockTransport.

    Raises:
        httpx2.HTTPError: If Alertmanager cannot be reached or answers with an error.
    """
    params = [("active", "true"), ("silenced", "false"), ("inhibited", "false")]
    params += [("filter", matcher) for matcher in filters or []]
    async with httpx2.AsyncClient(timeout=timeout, transport=transport) as client:
        response = await client.get(f"{alertmanager_url}/api/v2/alerts", params=params)
    response.raise_for_status()
    alerts = response.json()
    return alerts if isinstance(alerts, list) else []


async def fetch_alerts_fired_since(prometheus_url: str, seconds: int, timeout: float, transport=None) -> list[dict]:
    """Alerts that fired at any time in the last `seconds`, from Prometheus' ALERTS series.

    Alertmanager only knows what fires now; Prometheus keeps the history, so the
    morning report can also show alerts that fired overnight and stopped.

    Args:
        prometheus_url: Prometheus base URL.
        seconds: Length of the window, ending now (at least 60).
        timeout: Seconds for the request.
        transport: httpx2 transport; tests pass a MockTransport.

    Returns:
        One label dict per alert (alertname, severity, namespace, ...).

    Raises:
        httpx2.HTTPError: Prometheus cannot be reached or answers with an error.
        ValueError: Prometheus answered, but not with a successful query result.
    """
    query = f'max by (alertname, severity, namespace, pod, node, job) (max_over_time(ALERTS{{alertstate="firing"}}[{max(60, seconds)}s]))'
    async with httpx2.AsyncClient(timeout=timeout, transport=transport) as client:
        response = await client.get(f"{prometheus_url}/api/v1/query", params={"query": query})
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "success":
        raise ValueError(f"Prometheus query failed: {body.get('error', 'unknown error')}")
    return [series.get("metric", {}) for series in (body.get("data") or {}).get("result", [])]


def format_alerts(alerts: list[dict]) -> str:
    """One line per alert, most severe first."""
    if not alerts:
        return "(no firing alerts)"

    def order(alert):
        """Sort key: most severe first, then by name."""
        labels = alert.get("labels", {})
        return _SEVERITY_ORDER.get(labels.get("severity", ""), 3), labels.get("alertname", "")

    lines = []
    for alert in sorted(alerts, key=order):
        labels, annotations = alert.get("labels", {}), alert.get("annotations", {})
        where = " ".join(f"{key}={labels[key]}" for key in ("namespace", "pod", "node", "job") if labels.get(key))
        text = annotations.get("summary") or annotations.get("description") or ""
        lines.append(
            f"{labels.get('alertname', '?')} [{labels.get('severity', 'none')}] {where} "
            f"since {alert.get('startsAt', '?')}: {text}".replace("  ", " ").strip()
        )
    return "\n".join(lines)


async def check_ready(url: str, timeout: float, transport=None) -> str | None:
    """Return None when `{url}/-/ready` answers 200, otherwise a short reason (for doctor).

    Args:
        url: Prometheus or Alertmanager base URL.
        timeout: Seconds for the request.
        transport: httpx2 transport; tests pass a MockTransport.
    """
    try:
        async with httpx2.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(f"{url}/-/ready")
    except httpx2.HTTPError as exc:
        return f"{type(exc).__name__}: {exc}"
    return None if response.status_code == 200 else f"HTTP {response.status_code}"


def build_prometheus_tools(
    prometheus_url: str, alertmanager_url: str, timeout_seconds: float, transport=None
) -> list[Tool]:
    """Create the tools whose URL is configured.

    Args:
        prometheus_url: Prometheus base URL, or '' to skip the PromQL tools.
        alertmanager_url: Alertmanager base URL, or '' to skip the alerts tool.
        timeout_seconds: Timeout of one HTTP request.
        transport: httpx2 transport override (tests use httpx2.MockTransport).
    """
    tools: list[Tool] = []
    if prometheus_url:

        async def query(arguments: dict) -> str:
            """The prometheus_query tool: one instant query."""
            promql = str(arguments.get("query") or "").strip()
            if not promql:
                return "ERROR: 'query' is required"
            params = {"query": promql}
            if arguments.get("time"):
                params["time"] = str(arguments["time"]).strip()
            return await _prometheus_get(prometheus_url, "/api/v1/query", params, timeout_seconds, transport)

        async def query_range(arguments: dict) -> str:
            """The prometheus_query_range tool: a query over a time range, summarised per series."""
            keys = ("query", "start", "end", "step")
            missing = [key for key in keys if not str(arguments.get(key) or "").strip()]
            if missing:
                return f"ERROR: missing {', '.join(missing)}"
            params = {key: str(arguments[key]).strip() for key in keys}
            return await _prometheus_get(prometheus_url, "/api/v1/query_range", params, timeout_seconds, transport)

        tools.append(
            Tool(
                name="prometheus_query",
                description=(
                    "Run an instant PromQL query against the cluster's Prometheus and return the current value of "
                    "each matching series, e.g. sum by (namespace) (kube_pod_container_status_restarts_total). "
                    "Optional 'time' (RFC3339 or Unix seconds) queries a past moment. Load the prometheus-queries "
                    "skill for proven queries. An empty result is not proof that all is well."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "PromQL expression"},
                        "time": {"type": "string", "description": "Optional evaluation time"},
                    },
                    "required": ["query"],
                },
                run=query,
                surface="prometheus",
            )
        )
        tools.append(
            Tool(
                name="prometheus_query_range",
                description=(
                    "Run a PromQL query over a time range. Each series is summarised as first/last/min/max with "
                    "timestamps, which answers 'when did it start' and 'is it getting worse'. start and end are "
                    "RFC3339 (2026-10-12T06:00:00Z) or Unix seconds; step like 5m."
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "start": {"type": "string"},
                        "end": {"type": "string"},
                        "step": {"type": "string"},
                    },
                    "required": ["query", "start", "end", "step"],
                },
                run=query_range,
                surface="prometheus",
            )
        )
    if alertmanager_url:

        async def alerts(arguments: dict) -> str:
            """The alertmanager_alerts tool: alerts firing now, optionally filtered."""
            filters = arguments.get("filter") or []
            if isinstance(filters, str):
                filters = [filters]
            return format_alerts(await fetch_firing_alerts(alertmanager_url, timeout_seconds, filters, transport))

        tools.append(
            Tool(
                name="alertmanager_alerts",
                description=(
                    "List the alerts firing right now in Alertmanager (not silenced or inhibited), most severe first. "
                    'Optional \'filter\' is a list of label matchers, e.g. ["namespace=\\"kube-system\\""].'
                ),
                parameters={
                    "type": "object",
                    "properties": {"filter": {"type": "array", "items": {"type": "string"}}},
                },
                run=alerts,
                surface="prometheus",
            )
        )
    return tools
