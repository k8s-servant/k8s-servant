"""Compact cluster summary tools: `cluster_problems` and `warning_events`.

Why these exist: on a real cluster (615 Deployments, 1,252 pods, 3,700
Warning events) listing objects through MCP or kubectl gives the model far more
text than it can read. Lists were cut at the output limit, the event list did not
even fit into one MCP message, and the model missed real problems. These tools do
the boring part in Python instead: they read the objects as JSON and return
**only what is unhealthy**, plus how much was checked, in a few kilobytes. The
model then investigates the problems with MCP and kubectl.

Safety: both tools run fixed `kubectl get <resource> -o json` commands. The model
can only choose a namespace, an event reason and a time window, and each is
validated against a strict pattern before it gets near the command line, so no
model text can become a kubectl flag. Secrets are never requested.

The checks are plain functions over Kubernetes JSON so they can be tested without
a cluster; build_cluster_tools() wires them to kubectl.
"""

import asyncio
import json
import logging
import re
import shlex
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime, timedelta

from k8s_servant.tools.registry import Tool

logger = logging.getLogger(__name__)

# Reads `kubectl` arguments and returns the parsed JSON output.
Fetch = Callable[[list[str]], Awaitable[dict]]

# A container restarting this often, with its last restart within RESTART_WINDOW,
# is reported even if it is running right now (a slow crash loop).
RESTART_THRESHOLD = 5
RESTART_WINDOW = timedelta(hours=24)
# Failed Jobs older than this are history, not a current problem.
JOB_WINDOW = timedelta(hours=24)
# A quota used at or above this share of a limit is reported.
QUOTA_WARN_RATIO = 0.9
# Lines per section of cluster_problems and groups in warning_events.
MAX_LINES = 50
MAX_EVENT_GROUPS = 60

_NAMESPACE = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")
_REASON = re.compile(r"^[A-Za-z][A-Za-z0-9]*$")

# Binary (Ki = 1024) and decimal (k = 1000) suffixes of Kubernetes quantities.
_SUFFIXES = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
    "Pi": 1024**5,
    "Ei": 1024**6,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "k": 1e3,
    "M": 1e6,
    "G": 1e9,
    "T": 1e12,
    "P": 1e15,
    "E": 1e18,
}


def parse_quantity(text: str) -> float:
    """Convert a Kubernetes quantity ("500m", "4Gi", "2", "1e3") to a number.

    Raises:
        ValueError: If the text is not a quantity.
    """
    text = str(text).strip()
    for suffix in sorted(_SUFFIXES, key=len, reverse=True):
        if text.endswith(suffix) and not text.endswith("e" + suffix):
            return float(text[: -len(suffix)]) * _SUFFIXES[suffix]
    return float(text)


def _parse_time(value: str | None) -> datetime | None:
    """Parse a Kubernetes timestamp (RFC 3339, optional fractions); None if missing."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _fmt_time(when: datetime | None, now: datetime) -> str:
    """HH:MM UTC for today, the full date otherwise."""
    if when is None:
        return "unknown time"
    when = when.astimezone(UTC)
    return when.strftime("%H:%M UTC") if when.date() == now.date() else when.strftime("%Y-%m-%d %H:%M UTC")


def _one_line(text: str | None, limit: int = 200) -> str:
    """Collapse whitespace and newlines, and shorten."""
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[:limit] + "..."


def _ref(obj: dict) -> str:
    """`namespace/name` of a Kubernetes object."""
    meta = obj.get("metadata", {})
    return f"{meta.get('namespace', '')}/{meta.get('name', '')}"


# --- checks: each returns Markdown list lines ("- ...") --------------------------


def node_problems(nodes: list[dict]) -> list[str]:
    """Nodes that are not Ready, under pressure, or cordoned."""
    lines = []
    for node in nodes:
        name = node.get("metadata", {}).get("name", "")
        issues = []
        for cond in node.get("status", {}).get("conditions", []):
            kind, status = cond.get("type"), cond.get("status")
            if kind == "Ready" and status != "True":
                since = cond.get("lastTransitionTime", "unknown time")
                issues.append(f"NotReady (Ready={status} since {since}, {cond.get('reason', 'no reason')})")
            elif (kind != "Ready" and kind.endswith("Pressure") and status == "True") or (
                kind == "NetworkUnavailable" and status == "True"
            ):
                issues.append(kind)
        if node.get("spec", {}).get("unschedulable"):
            issues.append("cordoned (unschedulable)")
        if issues:
            lines.append(f"- Node {name}: {', '.join(issues)}")
    return lines


def _owner(pod: dict) -> tuple[str, str]:
    """The workload a pod belongs to: (kind, name).

    A ReplicaSet named <deployment>-<pod-template-hash> stands for its Deployment,
    so the result matches the finding fingerprints (owner, not pod).
    """
    refs = pod.get("metadata", {}).get("ownerReferences") or []
    if not refs:
        return "Pod", pod.get("metadata", {}).get("name", "")
    kind, name = refs[0].get("kind", ""), refs[0].get("name", "")
    if kind == "ReplicaSet" and "-" in name:
        return "Deployment", name.rsplit("-", 1)[0]
    return kind, name


def _pod_issue(pod: dict, now: datetime) -> str | None:
    """Why a pod is unhealthy, in a few words; None when it is fine."""
    status = pod.get("status", {})
    phase = status.get("phase", "Unknown")
    if phase == "Succeeded":
        return None
    if phase == "Failed":
        return f"Failed ({status.get('reason') or 'no reason'})"
    containers = status.get("initContainerStatuses", []) + status.get("containerStatuses", [])
    for container in containers:
        waiting = container.get("state", {}).get("waiting")
        if waiting and waiting.get("reason") not in (None, "PodInitializing"):
            return f"{waiting['reason']} (container {container.get('name')})"
    if phase == "Pending":
        for cond in status.get("conditions", []):
            if cond.get("type") == "PodScheduled" and cond.get("status") == "False":
                return f"Pending ({cond.get('reason', '')}: {_one_line(cond.get('message'), 150)})"
        return "Pending"
    if phase != "Running":
        return f"phase {phase}"
    for container in status.get("containerStatuses", []):
        last = container.get("lastState", {}).get("terminated")
        finished = _parse_time(last.get("finishedAt")) if last else None
        restarts = container.get("restartCount", 0)
        if restarts >= RESTART_THRESHOLD and finished and now - finished <= RESTART_WINDOW:
            return f"{restarts} restarts, last {last.get('reason', 'unknown')} at {_fmt_time(finished, now)}"
    for container in status.get("containerStatuses", []):
        if not container.get("ready", False):
            return f"not ready (container {container.get('name')})"
    return None


def pod_problems(pods: list[dict], now: datetime | None = None) -> list[str]:
    """Unhealthy pods, one line per owning workload (Deployment, StatefulSet, ...)."""
    now = now or datetime.now(UTC)
    groups: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
    for pod in pods:
        meta = pod.get("metadata", {})
        if meta.get("deletionTimestamp"):
            continue  # terminating: going away anyway
        issue = _pod_issue(pod, now)
        if issue:
            kind, name = _owner(pod)
            groups.setdefault((kind, meta.get("namespace", ""), name), []).append((meta.get("name", ""), issue))
    lines = []
    for (kind, namespace, name), members in groups.items():
        issues = list(dict.fromkeys(issue for _, issue in members))  # unique, in order
        lines.append(
            f"- {kind} {namespace}/{name}: {len(members)} of its pods unhealthy: "
            f"{'; '.join(issues[:3])}; e.g. pod {members[0][0]}"
        )
    return lines


def _bad_conditions(obj: dict) -> list[str]:
    """Deployment conditions that explain a stuck rollout (ReplicaFailure, Progressing=False)."""
    text = []
    for cond in obj.get("status", {}).get("conditions", []):
        failed = (cond.get("type") == "ReplicaFailure" and cond.get("status") == "True") or (
            cond.get("type") == "Progressing" and cond.get("status") == "False"
        )
        if failed:
            text.append(f"{cond['type']} {cond.get('reason', '')}: {_one_line(cond.get('message'))}")
    return text


def controller_problems(deployments: list[dict], statefulsets: list[dict], daemonsets: list[dict]) -> list[str]:
    """Workloads with fewer ready pods than desired, or a failing rollout."""
    lines = []
    for dep in deployments:
        desired = dep.get("spec", {}).get("replicas", 1)
        available = dep.get("status", {}).get("availableReplicas", 0)
        conditions = _bad_conditions(dep)
        if (desired > 0 and available < desired) or conditions:
            lines.append("; ".join([f"- Deployment {_ref(dep)}: {available}/{desired} available", *conditions]))
    for sts in statefulsets:
        desired = sts.get("spec", {}).get("replicas", 1)
        ready = sts.get("status", {}).get("readyReplicas", 0)
        if desired > 0 and ready < desired:
            lines.append(f"- StatefulSet {_ref(sts)}: {ready}/{desired} ready")
    for ds in daemonsets:
        desired = ds.get("status", {}).get("desiredNumberScheduled", 0)
        ready = ds.get("status", {}).get("numberReady", 0)
        if ready < desired:
            lines.append(f"- DaemonSet {_ref(ds)}: {ready}/{desired} ready")
    return lines


def pdb_problems(pdbs: list[dict]) -> list[str]:
    """PodDisruptionBudgets that match no pods or have too few healthy pods.

    A healthy single-replica budget (0 disruptions allowed) only blocks node drains;
    that is a design choice, not a problem, so it is not reported here.
    """
    lines = []
    for pdb in pdbs:
        status = pdb.get("status", {})
        expected = status.get("expectedPods", 0)
        healthy, needed = status.get("currentHealthy", 0), status.get("desiredHealthy", 0)
        if expected == 0:
            lines.append(
                f"- PodDisruptionBudget {_ref(pdb)}: matches no pods (expectedPods 0; it protects nothing, "
                "and 'not enough healthy pods' alerts fire for it)"
            )
        elif healthy < needed:
            lines.append(f"- PodDisruptionBudget {_ref(pdb)}: {healthy} healthy, needs {needed}")
    return lines


def quota_problems(quotas: list[dict], warn_ratio: float = QUOTA_WARN_RATIO) -> list[str]:
    """ResourceQuotas with a resource used at or above warn_ratio of its limit."""
    lines = []
    for quota in quotas:
        hard = quota.get("status", {}).get("hard", {})
        used = quota.get("status", {}).get("used", {})
        items = []
        for resource, limit_text in hard.items():
            try:
                limit = parse_quantity(limit_text)
                usage = parse_quantity(used.get(resource, "0"))
            except ValueError:
                continue
            if limit == 0:
                if usage > 0:
                    items.append(f"{resource} {used[resource]} used, limit 0")
                continue  # a zero limit that is unused is a deliberate block, not a problem
            ratio = usage / limit
            if ratio >= warn_ratio:
                items.append(f"{resource} {used.get(resource, '0')} of {limit_text} used ({round(ratio * 100)}%)")
        if items:
            lines.append(f"- ResourceQuota {_ref(quota)}: {', '.join(items)}")
    return lines


def pvc_problems(pvcs: list[dict]) -> list[str]:
    """PersistentVolumeClaims that are not Bound."""
    return [
        f"- PersistentVolumeClaim {_ref(pvc)}: {pvc.get('status', {}).get('phase', 'Unknown')}"
        for pvc in pvcs
        if pvc.get("status", {}).get("phase") != "Bound"
    ]


def job_problems(jobs: list[dict], now: datetime | None = None, window: timedelta = JOB_WINDOW) -> list[str]:
    """Jobs that failed within the window, with their CronJob if they have one.

    Args:
        jobs: Job objects (JSON).
        now: Current time; injectable for tests.
        window: Older failures are history, not a current problem.
    """
    now = now or datetime.now(UTC)
    lines = []
    for job in jobs:
        for cond in job.get("status", {}).get("conditions", []):
            if cond.get("type") != "Failed" or cond.get("status") != "True":
                continue
            when = _parse_time(cond.get("lastTransitionTime"))
            if when is None or now - when > window:
                continue
            refs = job.get("metadata", {}).get("ownerReferences") or []
            owner = f" ({refs[0].get('kind')} {refs[0].get('name')})" if refs else ""
            detail = ": ".join(filter(None, [cond.get("reason"), _one_line(cond.get("message"), 150)]))
            # Full date: the 24 h window crosses midnight.
            lines.append(f"- Job {_ref(job)}{owner}: failed {when.astimezone(UTC):%Y-%m-%d %H:%M} UTC: {detail}")
    return lines


# --- the tools --------------------------------------------------------------------

# (resource, label in the coverage line, cluster-scoped)
_RESOURCES = (
    ("nodes", "nodes", True),
    ("pods", "pods", False),
    ("deployments", "deployments", False),
    ("statefulsets", "statefulsets", False),
    ("daemonsets", "daemonsets", False),
    ("poddisruptionbudgets", "PDBs", False),
    ("resourcequotas", "quotas", False),
    ("persistentvolumeclaims", "PVCs", False),
    ("jobs", "jobs", False),
)


def _scope_args(namespace: str | None) -> list[str]:
    """kubectl arguments for one namespace (`-n ns`) or all of them (`-A`)."""
    return ["-n", namespace] if namespace else ["-A"]


def _section(title: str, lines: list[str], max_lines: int) -> str:
    """A Markdown section with at most `max_lines` lines, and how many more there are."""
    shown = lines[:max_lines]
    if len(lines) > max_lines:
        shown.append(f"- ... {len(lines) - max_lines} more; call again with a namespace to see them")
    return f"## {title}\n" + "\n".join(shown)


async def cluster_problems(
    fetch: Fetch, namespace: str | None = None, now: datetime | None = None, max_lines: int = MAX_LINES
) -> str:
    """Read the main object types and return only what is unhealthy, with coverage.

    Args:
        fetch: Runs `kubectl` arguments and returns parsed JSON.
        namespace: Limit the sweep to one namespace (nodes are then skipped).
        now: Current time; injectable for tests.
        max_lines: Lines per section before the rest is summarised.
    """
    now = now or datetime.now(UTC)
    wanted = [r for r in _RESOURCES if not (namespace and r[2])]

    async def get(resource: str, cluster_scoped: bool):
        """List one resource type (in the requested scope) and return its items."""
        args = ["get", resource, *([] if cluster_scoped else _scope_args(namespace)), "-o", "json"]
        return (await fetch(args)).get("items", [])

    results = await asyncio.gather(*(get(r, scoped) for r, _, scoped in wanted), return_exceptions=True)
    items: dict[str, list] = {}
    errors = []
    for (resource, _, _), result in zip(wanted, results, strict=True):
        if isinstance(result, BaseException):
            errors.append(f"Could not check {resource}: {type(result).__name__}: {result}")
            items[resource] = []
        else:
            items[resource] = result
    sections = [
        ("Nodes", node_problems(items.get("nodes", []))),
        ("Pods (grouped by owning workload)", pod_problems(items["pods"], now)),
        ("Workload controllers", controller_problems(items["deployments"], items["statefulsets"], items["daemonsets"])),
        ("PodDisruptionBudgets", pdb_problems(items["poddisruptionbudgets"])),
        ("ResourceQuotas (at or above 90% of a limit)", quota_problems(items["resourcequotas"])),
        ("PersistentVolumeClaims", pvc_problems(items["persistentvolumeclaims"])),
        ("Jobs failed in the last 24 h", job_problems(items["jobs"], now)),
    ]
    failed = {e.split(":")[0].removeprefix("Could not check ") for e in errors}
    checked = ", ".join(f"{len(items[r])} {label}" for r, label, _ in wanted if r not in failed)
    scope = f"namespace {namespace}" if namespace else "all namespaces"
    total = sum(len(lines) for _, lines in sections)
    parts = [
        f"Cluster problem sweep, {scope}, {now.strftime('%Y-%m-%d %H:%M UTC')}\n"
        f"Checked: {checked}\nProblems found: {total}"
    ]
    parts += [_section(title, lines, max_lines) for title, lines in sections if lines]
    parts += errors
    if total:
        parts.append("Investigate each problem further (describe, events, logs) before reporting it.")
    return "\n\n".join(parts)


def _event_time(event: dict) -> datetime | None:
    """When an event last happened.

    Old-style events have lastTimestamp, new-style ones a series or only eventTime;
    the creation time is the last resort.
    """
    series = event.get("series") or {}
    for value in (
        event.get("lastTimestamp"),
        series.get("lastObservedTime"),
        event.get("eventTime"),
        event.get("metadata", {}).get("creationTimestamp"),
    ):
        when = _parse_time(value)
        if when:
            return when
    return None


async def warning_events(
    fetch: Fetch,
    namespace: str | None = None,
    since_minutes: int = 60,
    reason: str | None = None,
    include_excluded: bool = False,
    exclude_reasons: Iterable[str] = (),
    now: datetime | None = None,
    max_groups: int = MAX_EVENT_GROUPS,
) -> str:
    """Warning events grouped by (namespace, object, reason), most frequent first.

    Reasons in exclude_reasons are filtered out by the API server (field selector),
    so thousands of policy-scan events never leave the cluster API.

    Args:
        fetch: Runs `kubectl` arguments and returns parsed JSON.
        namespace: Only this namespace (already validated); None = all.
        since_minutes: Ignore events whose last occurrence is older.
        reason: Only this reason (already validated); disables exclude_reasons.
        include_excluded: Also show the excluded reasons.
        exclude_reasons: Reasons hidden by default (EVENTS_EXCLUDE_REASONS).
        now: Current time; injectable for tests.
        max_groups: Groups shown before the rest is summarised.
    """
    now = now or datetime.now(UTC)
    exclude = [] if (reason or include_excluded) else list(exclude_reasons)
    selector = ",".join(["type=Warning", *([f"reason={reason}"] if reason else []), *(f"reason!={r}" for r in exclude)])
    data = await fetch(["get", "events", *_scope_args(namespace), "--field-selector", selector, "-o", "json"])
    since = now - timedelta(minutes=since_minutes)
    groups: dict[tuple, dict] = {}
    for event in data.get("items", []):
        when = _event_time(event)
        if when is None or when < since:
            continue
        obj = event.get("involvedObject", {})
        key = (
            event.get("metadata", {}).get("namespace", ""),
            obj.get("kind", ""),
            obj.get("name", ""),
            event.get("reason", ""),
        )
        count = event.get("count") or (event.get("series") or {}).get("count") or 1
        group = groups.setdefault(key, {"count": 0, "last": when, "message": event.get("message", "")})
        group["count"] += count
        if when >= group["last"]:
            group["last"], group["message"] = when, event.get("message", "")
    ordered = sorted(groups.items(), key=lambda item: (-item[1]["count"], -item[1]["last"].timestamp()))
    scope = f"namespace {namespace}" if namespace else "all namespaces"
    total = sum(g["count"] for g in groups.values())
    lines = [f"{total} Warning events in {len(groups)} groups, last {since_minutes} min, {scope}."]
    if exclude:
        lines.append(f"Hidden reasons: {', '.join(exclude)} (pass include_excluded=true or reason=<name> to see them).")
    for (ns, kind, name, why), group in ordered[:max_groups]:
        lines.append(
            f"- {ns} {kind}/{name} {why} x{group['count']}, last {_fmt_time(group['last'], now)}: "
            f"{_one_line(group['message'])}"
        )
    if len(ordered) > max_groups:
        lines.append(f"- ... {len(ordered) - max_groups} more groups; narrow with namespace or reason")
    return "\n".join(lines)


async def kubectl_json(args: list[str], kubectl_path: str, timeout_seconds: int) -> dict:
    """Run a fixed, trusted kubectl command and parse its JSON output.

    Only for commands built by this module, never for model input (see
    kubectl_tool.check_kubectl_args() for that).

    Args:
        args: kubectl arguments, e.g. ["get", "pods", "-A", "-o", "json"].
        kubectl_path: kubectl binary.
        timeout_seconds: Maximum duration.

    Raises:
        RuntimeError: kubectl failed (the last line of its error output is kept).
        TimeoutError: kubectl did not finish in time.
    """
    logger.info("kubectl %s", shlex.join(args))
    process = await asyncio.create_subprocess_exec(
        kubectl_path, *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise TimeoutError(f"kubectl {args[1]} took longer than {timeout_seconds}s") from None
    if process.returncode != 0:
        lines = stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise RuntimeError(lines[-1] if lines else f"kubectl exited with code {process.returncode}")
    return json.loads(stdout or b"{}")


def _check_namespace(value) -> str | None:
    """Validate a namespace argument from the model before it reaches kubectl.

    Returns:
        The namespace, or None for all namespaces.

    Raises:
        ValueError: Not a valid namespace name (could otherwise smuggle in a flag).
    """
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > 63 or not _NAMESPACE.match(value):
        raise ValueError(f"namespace {value!r} is not a valid namespace name")
    return value


def build_cluster_tools(kubectl_path: str, timeout_seconds: int, exclude_reasons: Iterable[str]) -> list[Tool]:
    """Create the `cluster_problems` and `warning_events` tools.

    Args:
        kubectl_path: kubectl binary.
        timeout_seconds: Maximum duration of one kubectl call.
        exclude_reasons: Event reasons hidden by default (EVENTS_EXCLUDE_REASONS).
    """
    exclude_reasons = tuple(exclude_reasons)

    async def fetch(args: list[str]) -> dict:
        """Run a fixed kubectl command and parse its JSON."""
        return await kubectl_json(args, kubectl_path, timeout_seconds)

    async def run_problems(arguments: dict) -> str:
        """The cluster_problems tool: validate the namespace, then sweep."""
        try:
            namespace = _check_namespace(arguments.get("namespace"))
        except ValueError as exc:
            return f"ERROR: {exc}"
        return await cluster_problems(fetch, namespace)

    async def run_events(arguments: dict) -> str:
        """The warning_events tool: validate every argument, then read and group events."""
        try:
            namespace = _check_namespace(arguments.get("namespace"))
            reason = arguments.get("reason") or None
            if reason is not None and (not isinstance(reason, str) or not _REASON.match(reason)):
                raise ValueError(f"reason {reason!r} must be one event reason, e.g. FailedScheduling")
            since = arguments.get("since_minutes", 60)
            if isinstance(since, bool) or not isinstance(since, int) or not 1 <= since <= 10080:
                raise ValueError("since_minutes must be a whole number from 1 to 10080")
        except ValueError as exc:
            return f"ERROR: {exc}"
        try:
            return await warning_events(
                fetch, namespace, since, reason, bool(arguments.get("include_excluded")), exclude_reasons
            )
        except (RuntimeError, TimeoutError, json.JSONDecodeError) as exc:
            return f"ERROR: could not read events: {exc}"

    namespace_schema = {"type": "string", "description": "Only this namespace (optional; default all namespaces)"}
    hidden = ", ".join(exclude_reasons) or "none"
    return [
        Tool(
            name="cluster_problems",
            description=(
                "START HERE for any health question. Reads nodes, pods, Deployments, StatefulSets, "
                "DaemonSets, PodDisruptionBudgets, ResourceQuotas, PVCs and Jobs at once and returns ONLY "
                "what is unhealthy (pods grouped by owning workload), plus how many objects were checked. "
                "A few KB instead of the full lists, so use it instead of listing all pods or deployments."
            ),
            parameters={"type": "object", "properties": {"namespace": namespace_schema}},
            run=run_problems,
            surface="cluster",
        ),
        Tool(
            name="warning_events",
            description=(
                "Warning events grouped by object and reason, most frequent first, with counts and the "
                f"latest message. Use it instead of listing raw events. Hidden by default: {hidden} "
                "(policy scanners emit thousands of these); pass include_excluded=true or reason=<name> "
                "to see them."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "namespace": namespace_schema,
                    "since_minutes": {"type": "integer", "description": "Time window, default 60"},
                    "reason": {"type": "string", "description": "Only this reason, e.g. FailedScheduling"},
                    "include_excluded": {"type": "boolean", "description": "Also show the hidden reasons"},
                },
            },
            run=run_events,
            surface="cluster",
        ),
    ]
