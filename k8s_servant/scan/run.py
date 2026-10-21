"""One scan of a cluster: read the inputs once, run every detector, reach monitoring.

Why: the detectors (catalog, platform, network, monitoring, noise) each judge one
part of the cluster; this module runs them in order on the same inputs and reaches
Prometheus and Alertmanager the best way it can (a configured URL, else the API
server's service proxy). Nothing in it raises for a cluster that cannot be read:
what failed is listed in the result instead.
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from k8s_servant.findings import StoredFinding
from k8s_servant.memory import Memory
from k8s_servant.scan.catalog import component_facts
from k8s_servant.scan.discover import (
    MONITORING_TOOLS,
    _utc,
    monitoring_facts,
    monitoring_services,
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


def _guard[T](family: str, failed: dict[str, str], detect: Callable[[], T], default: T) -> T:
    """Run one detector family; a bug in it costs its facts, never the scan."""
    try:
        return detect()
    except Exception as exc:
        failed[f"detector:{family}"] = type(exc).__name__
        log.warning("The %s detector failed (%s); its facts are left out", family, type(exc).__name__)
        return default


async def _reach(
    tool: str,
    configured: Callable | None,
    proxied: Callable | None,
    target: tuple[str, str, int] | None,
    read: Callable[[Callable], Awaitable],
    failed: dict[str, str],
) -> tuple[object | None, bool, str]:
    """Read through the configured URL, else through the service proxy.

    Returns the answer (None when neither worked), whether the proxy answered, and
    the source that answered, for the evidence. A configured URL that fails is
    recorded as `<tool>-url` (by exception type only: the URL may hold a credential)
    before the proxy is tried, so falling back never goes unnoticed.
    """
    label = MONITORING_TOOLS[tool][0]
    if configured is not None:
        try:
            return await read(configured), False, f"{label} at the configured URL"
        except Exception as exc:
            failed[f"{tool}-url"] = f"configured URL failed ({type(exc).__name__})"
            log.warning("%s at the configured URL failed (%s); trying the service proxy", label, type(exc).__name__)
    if proxied is not None and target is not None:
        namespace, name, _ = target
        try:
            return await read(proxied), True, f"{label} through the API server proxy, service {namespace}/{name}"
        except Exception as exc:
            log.warning("%s through the service proxy failed (%s)", label, type(exc).__name__)
    failed[tool] = NOT_REACHABLE
    return None, False, label


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

    Never raises for a cluster that cannot be read or a detector that fails: both land
    in `failed`.
    """
    now = _utc((clock or (lambda: datetime.now(UTC)))())
    inputs = await gather_inputs(fetch)
    failed = dict(inputs.failed)
    components, namespaces = _guard("components", failed, lambda: component_facts(inputs), ([], set()))
    facts = [
        *components,
        *_guard("platform", failed, lambda: platform_facts(inputs), []),
        *_guard("organisation", failed, lambda: organisation_facts(inputs, namespaces), []),
        *_guard("network", failed, lambda: network_facts(inputs), []),
    ]
    timeout = config["prometheus_timeout_seconds"]

    # A proxy target only when exactly one Service qualifies; several are asked about.
    def target(tool: str) -> tuple[str, str, int] | None:
        found = _guard("monitoring", failed, lambda: monitoring_services(inputs, tool), [])
        return found[0] if len(found) == 1 else None

    found = target("prometheus")
    shares, prometheus_checked, prometheus_source = await _reach(
        "prometheus",
        direct_prometheus(config["prometheus_url"], timeout, transport) if config["prometheus_url"] else None,
        proxied_prometheus(fetch, *found) if found else None,
        found,
        alert_shares,
        failed,
    )
    found = target("alertmanager")
    firing, alertmanager_checked, alertmanager_source = await _reach(
        "alertmanager",
        direct_alertmanager(config["alertmanager_url"], timeout, transport) if config["alertmanager_url"] else None,
        proxied_alertmanager(fetch, *found) if found else None,
        found,
        _firing_now,
        failed,
    )
    facts += _guard(
        "monitoring", failed, lambda: monitoring_facts(inputs, prometheus_checked, alertmanager_checked), []
    )

    findings: list[StoredFinding] = []
    if memory is not None:
        try:
            findings = memory.open_findings()
        except Exception as exc:
            failed["memory"] = _reason(exc)
    facts += _guard(
        "noise",
        failed,
        lambda: noise_facts(
            inputs,
            config,
            shares,
            firing,
            findings,
            now,
            prometheus_source=prometheus_source,
            alertmanager_source=alertmanager_source,
        ),
        [],
    )
    facts.sort(key=lambda f: (TOPICS.index(f.topic), f.key))
    return ScanResult(scanned_at=now, cluster=cluster, facts=facts, failed=failed, kubectl_ok=bool(inputs.items))
