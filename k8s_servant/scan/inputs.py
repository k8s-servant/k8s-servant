"""The cluster inputs of a scan, each read once and judged on its own.

Why: every detector reads from the same few kubectl lists (nodes, workloads,
CRDs, ...). Reading each list once keeps the scan cheap on a large cluster, and
recording a refusal per input (not one exception for the whole scan) lets a
forbidden `networkpolicies` list cost only the facts that need it.

Also here: the two ways to reach Prometheus and Alertmanager (a configured URL,
or the API server's read-only service proxy) behind one callable shape each, and
the 7-day alert share query that tells recurring noise from real incidents.
"""

import asyncio
import urllib.parse
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx2

from k8s_servant.tools.cluster_tools import Fetch
from k8s_servant.tools.prometheus_tool import fetch_firing_alerts

# (name, kubectl args, CRD name the input needs or None). Never Secrets.
INPUTS: tuple[tuple[str, list[str], str | None], ...] = (
    ("version", ["version", "-o", "json"], None),
    ("nodes", ["get", "nodes", "-o", "json"], None),
    ("namespaces", ["get", "namespaces", "-o", "json"], None),
    ("crds", ["get", "customresourcedefinitions", "-o", "json"], None),
    ("ingressclasses", ["get", "ingressclasses", "-o", "json"], None),
    ("csidrivers", ["get", "csidrivers", "-o", "json"], None),
    ("mutatingwebhooks", ["get", "mutatingwebhookconfigurations", "-o", "json"], None),
    ("validatingwebhooks", ["get", "validatingwebhookconfigurations", "-o", "json"], None),
    ("services", ["get", "services", "-A", "-o", "json"], None),
    ("deployments", ["get", "deployments", "-A", "-o", "json"], None),
    ("daemonsets", ["get", "daemonsets", "-A", "-o", "json"], None),
    ("statefulsets", ["get", "statefulsets", "-A", "-o", "json"], None),
    ("pods", ["get", "pods", "-A", "-o", "json"], None),
    ("networkpolicies", ["get", "networkpolicies", "-A", "-o", "json"], None),
    ("ingresses", ["get", "ingresses", "-A", "-o", "json"], None),
    ("events", ["get", "events", "-A", "--field-selector", "type=Warning", "-o", "json"], None),
    (
        "gateways",
        ["get", "gateways.gateway.networking.k8s.io", "-A", "-o", "json"],
        "gateways.gateway.networking.k8s.io",
    ),
    (
        "gatewayclasses",
        ["get", "gatewayclasses.gateway.networking.k8s.io", "-o", "json"],
        "gatewayclasses.gateway.networking.k8s.io",
    ),
    (
        "calico-policies",
        ["get", "globalnetworkpolicies.crd.projectcalico.org", "-o", "json"],
        "globalnetworkpolicies.crd.projectcalico.org",
    ),
    (
        "cilium-policies",
        ["get", "ciliumclusterwidenetworkpolicies.cilium.io", "-o", "json"],
        "ciliumclusterwidenetworkpolicies.cilium.io",
    ),
)

# PromQL -> Prometheus API body; () -> firing alerts.
PromQuery = Callable[[str], Awaitable[dict]]
AlertsNow = Callable[[], Awaitable[list[dict]]]

# Share of the last 7 days (2016 five-minute steps) each alert was firing.
ALERT_SHARE_QUERY = 'count_over_time((max by (alertname) (ALERTS{alertstate="firing"}))[7d:5m]) / 2016'

_REASON_LIMIT = 200


@dataclass
class Inputs:
    """What the scan read: items per input, and why an input could not be read."""

    items: dict[str, list[dict]] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)

    def get(self, name: str) -> list[dict]:
        """Items of an input; empty when it was not read."""
        return self.items.get(name, [])

    def ok(self, name: str) -> bool:
        """Whether the input was read (an empty list counts)."""
        return name in self.items

    def crd_names(self) -> set[str]:
        """Names of the installed CRDs."""
        return {c.get("metadata", {}).get("name", "") for c in self.get("crds")} - {""}

    def crd_groups(self) -> set[str]:
        """API groups of the installed CRDs."""
        return {c.get("spec", {}).get("group", "") for c in self.get("crds")} - {""}


def _reason(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".splitlines()[0][:_REASON_LIMIT]


def _items(name: str, body: dict) -> list[dict]:
    if name == "version":
        return [body]
    return list(body.get("items") or [])


async def gather_inputs(fetch: Fetch) -> Inputs:
    """Read every input once: CRDs first, then the rest concurrently.

    An input whose CRD is not installed is skipped; when the CRD list itself could
    not be read, those inputs are recorded as failed. Never raises for an input.
    """
    result = Inputs()
    specs = {name: (args, crd) for name, args, crd in INPUTS}

    async def read(name: str) -> None:
        try:
            result.items[name] = _items(name, await fetch(specs[name][0]))
        except Exception as exc:
            result.failed[name] = _reason(exc)

    await read("crds")
    pending = []
    for name, (_, crd) in specs.items():
        if name == "crds":
            continue
        if crd is None:
            pending.append(name)
        elif not result.ok("crds"):
            result.failed[name] = "CRD list not readable"
        elif crd in result.crd_names():
            pending.append(name)
    await asyncio.gather(*(read(name) for name in pending), return_exceptions=True)
    return result


def direct_prometheus(url: str, timeout: float, transport=None) -> PromQuery:
    """Query Prometheus at a configured URL."""

    async def query(promql: str) -> dict:
        async with httpx2.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.get(f"{url}/api/v1/query", params={"query": promql})
        return response.json()

    return query


def direct_alertmanager(url: str, timeout: float, transport=None) -> AlertsNow:
    """Read firing alerts from Alertmanager at a configured URL."""

    async def alerts() -> list[dict]:
        return await fetch_firing_alerts(url, timeout, transport=transport)

    return alerts


def _proxy(namespace: str, service: str, port: int, path: str) -> list[str]:
    return ["get", "--raw", f"/api/v1/namespaces/{namespace}/services/{service}:{port}/proxy{path}"]


def proxied_prometheus(fetch: Fetch, namespace: str, service: str, port: int) -> PromQuery:
    """Query Prometheus through the API server's read-only service proxy."""

    async def query(promql: str) -> dict:
        quoted = urllib.parse.quote(promql, safe="")
        return await fetch(_proxy(namespace, service, port, f"/api/v1/query?query={quoted}"))

    return query


def proxied_alertmanager(fetch: Fetch, namespace: str, service: str, port: int) -> AlertsNow:
    """Read firing alerts through the API server's read-only service proxy."""

    async def alerts() -> list[dict]:
        path = "/api/v2/alerts?active=true&silenced=false&inhibited=false"
        body = await fetch(_proxy(namespace, service, port, path))
        return body if isinstance(body, list) else []

    return alerts


async def alert_shares(query: PromQuery) -> dict[str, float]:
    """Alert name -> share (0..1) of the last 7 days it was firing.

    Raises:
        ValueError: If Prometheus did not answer with status "success".
    """
    body = await query(ALERT_SHARE_QUERY)
    if body.get("status") != "success":
        raise ValueError(f"Prometheus query failed: {body.get('error', body.get('status'))}")
    shares = {}
    for row in (body.get("data") or {}).get("result", []):
        name = row.get("metric", {}).get("alertname")
        if name:
            shares[name] = float(row["value"][1])
    return shares
