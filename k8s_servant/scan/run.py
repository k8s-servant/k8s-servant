"""One scan of a cluster: read the inputs once, run every detector, reach monitoring.

Why: the detectors (catalog, platform, network, monitoring, noise) each judge one
part of the cluster; this module runs them in order on the same inputs and reaches
Prometheus and Alertmanager the best way it can (a configured URL, else the API
server's service proxy). Nothing in it raises for a cluster that cannot be read:
what failed is listed in the result instead.
"""

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from k8s_servant.findings import StoredFinding
from k8s_servant.memory import Memory
from k8s_servant.scan.catalog import component_facts
from k8s_servant.scan.discover import (
    _utc,
    find_service,
    monitoring_facts,
    network_facts,
    noise_facts,
    organisation_facts,
    platform_facts,
)
from k8s_servant.scan.facts import TOPICS, ScanResult
from k8s_servant.scan.inputs import (
    _reason,
    alert_shares,
    direct_alertmanager,
    direct_prometheus,
    gather_inputs,
    proxied_alertmanager,
    proxied_prometheus,
)
from k8s_servant.tools.cluster_tools import Fetch

log = logging.getLogger(__name__)


NOT_REACHABLE = "not reachable; noise comes from events and restarts only"


async def _alert_access(name: str, configured, proxied, read) -> tuple[object | None, bool, str]:
    """Read through the configured URL, else through the service proxy.

    Returns the answer (None when neither worked), whether the proxy answered, and
    why it failed. Any exception falls through to the next option.
    """
    if configured is not None:
        try:
            return await read(configured), False, ""
        except Exception as exc:
            log.warning("%s at the configured URL failed (%s); trying the service proxy", name, type(exc).__name__)
    if proxied is not None:
        try:
            return await read(proxied), True, ""
        except Exception as exc:
            log.warning("%s through the service proxy failed (%s)", name, type(exc).__name__)
    return None, False, NOT_REACHABLE


async def _firing_now(alerts) -> list[dict]:
    return [a for a in await alerts() if isinstance(a, dict)]


async def scan(
    fetch: Fetch,
    config: dict,
    cluster: str,
    memory: Memory | None = None,
    clock: Callable[[], datetime] | None = None,
    transport=None,
) -> ScanResult:
    """Scan a cluster once and return everything learned as facts.

    Args:
        fetch: Runs one read-only kubectl call and returns its parsed JSON.
        config: App settings from `configure_app()`.
        cluster: Name of the cluster, carried into the result.
        memory: Where open findings come from (`open_findings()`); None for none.
        clock: Returns the current time; tests pass a fixed one.
        transport: httpx2 transport for the configured URLs; tests pass a MockTransport.

    Never raises for an input that cannot be read: it lands in `failed`.
    """
    now = _utc((clock or (lambda: datetime.now(UTC)))())
    inputs = await gather_inputs(fetch)
    components, namespaces = component_facts(inputs)
    facts = [*components, *platform_facts(inputs), *organisation_facts(inputs, namespaces), *network_facts(inputs)]
    failed = dict(inputs.failed)
    timeout = config["prometheus_timeout_seconds"]
    services = inputs.get("services")

    found = find_service(services, "prometheus", 9090)
    shares, prometheus_checked, why = await _alert_access(
        "Prometheus",
        direct_prometheus(config["prometheus_url"], timeout, transport) if config["prometheus_url"] else None,
        proxied_prometheus(fetch, *found) if found else None,
        alert_shares,
    )
    if why:
        failed["prometheus"] = why

    found = find_service(services, "alertmanager", 9093)
    firing, alertmanager_checked, why = await _alert_access(
        "Alertmanager",
        direct_alertmanager(config["alertmanager_url"], timeout, transport) if config["alertmanager_url"] else None,
        proxied_alertmanager(fetch, *found) if found else None,
        _firing_now,
    )
    if why:
        failed["alertmanager"] = why

    facts += monitoring_facts(inputs, prometheus_checked, alertmanager_checked)
    findings: list[StoredFinding] = []
    if memory is not None:
        try:
            findings = memory.open_findings()
        except Exception as exc:
            failed["memory"] = _reason(exc)
    facts += noise_facts(inputs, config, shares, firing, findings, now)
    facts.sort(key=lambda f: (TOPICS.index(f.topic), f.key))
    return ScanResult(scanned_at=now, cluster=cluster, facts=facts, failed=failed, kubectl_ok=bool(inputs.items))
