"""One health check: ask the agent to sweep the cluster, then remember the result.

Flow:
  1. Build the request: "follow the cluster-health-check skill", plus the
     findings still open from earlier checks, so the model re-verifies them and
     reuses their fingerprints.
  2. The agent investigates with its tools and ends with a JSON block of findings.
  3. parse_findings() reads that block. If it is missing or invalid, the model
     gets one follow-up asking for just the JSON. If that also fails, the run is
     recorded as failed and memory is NOT changed: a broken answer must never mark
     every open problem as resolved.
  4. Memory compares with the open findings: new / still open / resolved.
  5. A Markdown report is rendered and stored with the run (`k8s-servant history`).

Whether to post to Teams is the caller's decision (cli.py, TEAMS_POST_MODE).

Skipping unchanged checks (run_health_check): a deep check costs 150k-225k model
tokens, while most hours nothing changes. Before calling the model, the summary
tools (cluster_problems, alertmanager_alerts) run without it, in a few seconds.
If their problem lines and alerts are identical to those at the last successful
full check, the run is recorded as unchanged and the open findings stay open. A full
check still runs at least every `full_interval`, because the summary cannot see
everything the model checks (Flux, node usage, events).
"""

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from k8s_servant.agent import Agent
from k8s_servant.findings import FindingsDiff, FindingsParseError, StoredFinding, parse_findings
from k8s_servant.memory import Memory
from k8s_servant.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

# Categories for fingerprints; a fixed list keeps fingerprints stable between runs.
CATEGORIES = (
    "crashloop",
    "pending",
    "oom",
    "image-pull",
    "not-ready",
    "rollout",
    "job-failed",
    "node-not-ready",
    "node-pressure",
    "capacity",
    "pvc",
    "flux-not-ready",
    "alert-firing",
    "quota",
    "pdb",
    "certificate",
    "other",
)

CHECK_PROMPT = """\
Run a health check of the whole cluster now. Load and follow the `cluster-health-check` skill.

Findings that were still open after the previous check. Re-verify each one; report it
again only if the problem still exists, and then reuse its exact fingerprint:
{open_findings}

When you have finished investigating, end your answer with exactly one JSON block in
this format (an empty "findings" list means the cluster is healthy):

```json
{{
  "summary": "One or two sentences on the overall health of the cluster.",
  "findings": [
    {{
      "fingerprint": "<category>/<namespace or _cluster>/<Kind>/<name>",
      "severity": "critical | warning | info",
      "title": "One line: what is wrong, e.g. Deployment api is crash-looping",
      "detail": "Evidence (object, field, event, time), the likely cause and the suggested fix"
    }}
  ]
}}
```

Fingerprint rules: the category is one of {categories}. Use the owning workload
(Deployment, StatefulSet, DaemonSet, Job, CronJob), not the pod, so that a recreated
pod keeps the same fingerprint. Use _cluster as the namespace for cluster-scoped
objects such as nodes. Report each problem once."""

FIX_JSON_PROMPT = (
    "Your answer did not end with a valid findings JSON block ({error}). Reply with only that "
    "JSON block, in exactly the format requested, and nothing else."
)


@dataclass
class CheckResult:
    """Outcome of one health check."""

    ok: bool  # False when the findings could not be parsed (memory unchanged)
    summary: str
    diff: FindingsDiff
    report_md: str
    run_id: int
    started_at: datetime
    duration_s: float
    error: str = ""


def format_when(moment: datetime) -> str:
    """A UTC timestamp for reports, e.g. 2026-10-12 06:00 UTC."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _indent(text: str) -> str:
    """Indent every line by two spaces (Markdown continuation of a list item)."""
    return "\n".join(f"  {line}" for line in text.splitlines())


def build_check_prompt(open_findings: list[StoredFinding]) -> str:
    """The user message that starts a health check."""
    if open_findings:
        lines = "\n".join(
            f"- `{f.fingerprint}` [{f.severity}] {f.title} (open since {format_when(f.first_seen)})"
            for f in open_findings
        )
    else:
        lines = "(none)"
    return CHECK_PROMPT.format(open_findings=lines, categories=", ".join(CATEGORIES))


def render_check_report(cluster_name: str, started_at: datetime, summary: str, diff: FindingsDiff) -> str:
    """The Markdown report of a successful check (stdout, logs and history).

    Args:
        cluster_name: For the title.
        started_at: When the check started (UTC).
        summary: The model's one- or two-sentence summary.
        diff: New, still open and resolved findings from Memory.apply_check().
    """
    parts = [
        f"# Health check — {cluster_name} — {format_when(started_at)}",
        summary or "(no summary)",
        f"**{len(diff.new)} new · {diff.open_count} open · {len(diff.resolved)} resolved**",
    ]
    if diff.new:
        items = []
        for f in diff.new:
            item = f"- **[{f.severity.upper()}] {f.title}** — `{f.fingerprint}`"
            items.append(f"{item}\n{_indent(f.detail)}" if f.detail else item)
        parts.append("## New\n\n" + "\n".join(items))
    if diff.ongoing:
        parts.append(
            "## Still open\n\n"
            + "\n".join(
                f"- [{f.severity}] {f.title} — open since {format_when(f.first_seen)} — `{f.fingerprint}`"
                for f in diff.ongoing
            )
        )
    if diff.resolved:
        parts.append(
            "## Resolved\n\n"
            + "\n".join(
                f"- {f.title} — was open since {format_when(f.first_seen)} — `{f.fingerprint}`" for f in diff.resolved
            )
        )
    if not (diff.new or diff.ongoing or diff.resolved):
        parts.append("No problems found.")
    return "\n\n".join(parts) + "\n"


def record_failed_run(memory: Memory, kind: str, started_at: datetime, error: str) -> int:
    """Store a run that failed before producing a result (e.g. the model was unreachable).

    Args:
        memory: The agent's memory.
        kind: Run kind (`check`, `morning`, `ask`).
        started_at: When the run started (UTC).
        error: Short, human-readable reason (model_error_text()).

    Returns:
        The run id.
    """
    duration = max(0.0, (datetime.now(UTC) - started_at).total_seconds())
    report = f"# {kind.capitalize()} failed — {format_when(started_at)}\n\n{error}\n"
    return memory.record_run(kind, started_at, duration, "failed", f"failed: {error}"[:500], report)


async def run_check(
    agent: Agent, memory: Memory, cluster_name: str, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> CheckResult:
    """Run one health check and update memory.

    Args:
        agent: A ready agent (runtime.open_agent()).
        memory: The agent's memory.
        cluster_name: For the report title.
        clock: Current time (injectable for tests).

    Returns:
        CheckResult. ok=False when the findings JSON could not be read.

    Raises:
        Whatever the model client raises (e.g. openai.APIConnectionError);
        callers record those with record_failed_run().
    """
    started_at = clock()
    t0 = time.monotonic()
    result = await agent.run(build_check_prompt(memory.open_findings()))
    try:
        summary, findings = parse_findings(result.answer)
    except FindingsParseError as first_error:
        logger.warning("The findings JSON was invalid (%s); asking the model once more", first_error)
        retry = await agent.follow_up(result.messages, FIX_JSON_PROMPT.format(error=first_error))
        try:
            summary, findings = parse_findings(retry)
        except FindingsParseError as error:
            duration = time.monotonic() - t0
            report = (
                f"# Health check failed — {cluster_name} — {format_when(started_at)}\n\n"
                f"The model's answer contained no valid findings JSON ({error}), so memory was not changed.\n\n"
                f"## The model's answer\n\n{result.answer}\n"
            )
            run_id = memory.record_run("check", started_at, duration, "failed", f"failed: {error}", report)
            logger.error("Health check failed: %s", error)
            return CheckResult(False, "", FindingsDiff([], [], []), report, run_id, started_at, duration, str(error))
    diff = memory.apply_check(findings, now=started_at)
    duration = time.monotonic() - t0
    report = render_check_report(cluster_name, started_at, summary, diff)
    run_id = memory.record_run(
        "check", started_at, duration, "ok", summary, report, len(diff.new), diff.open_count, len(diff.resolved)
    )
    logger.info(
        "Health check done in %.0fs: %d new, %d open, %d resolved",
        duration,
        len(diff.new),
        diff.open_count,
        len(diff.resolved),
    )
    return CheckResult(True, summary, diff, report, run_id, started_at, duration)


# Summary of a check that skipped the model; the morning report counts these runs.
SKIPPED_SUMMARY_PREFIX = "Unchanged since"

# Tools whose output decides whether anything changed, and the memory keys used.
SWEEP_TOOLS = ("cluster_problems", "alertmanager_alerts")
META_SWEEP_SIGNATURE = "last_sweep_signature"
META_FULL_CHECK_AT = "last_full_check_at"


def _alert_identity(line: str) -> str:
    """An alert line without its start time and text: name, severity and location.

    Summary texts often contain live values ("CPU requests are 93% of capacity")
    that would make every sweep look different.
    """
    return line.split(" since ", 1)[0].strip()


async def sweep_signature(registry: ToolRegistry, ignore_alerts: tuple[str, ...] = ()) -> str | None:
    """Fingerprint of the current problems and firing alerts, without the model.

    Only the problem lines of cluster_problems count (its header has a timestamp and
    object counts that change every minute), plus the identity of every firing alert
    except the always-firing ones in ignore_alerts (InfoInhibitor re-fires every few
    minutes with a new start time).

    Returns:
        A hash, or None when there is no summary tool or it could not read
        everything (then nothing proves that the cluster is unchanged).
    """
    names = {tool.name for tool in registry.tools()}
    if "cluster_problems" not in names:
        return None
    lines = []
    for name in SWEEP_TOOLS:
        if name not in names:
            continue
        output = await registry.run(name, "{}")
        if output.startswith("ERROR") or "Could not check" in output:
            return None
        if name == "cluster_problems":
            lines += [f"{name}: {line}" for line in output.splitlines() if line.startswith("- ")]
        else:
            lines += [
                f"{name}: {_alert_identity(line)}"
                for line in output.splitlines()
                if line.strip() and line.split(" ", 1)[0] not in ignore_alerts
            ]
    return hashlib.sha256("\n".join(sorted(lines)).encode()).hexdigest()


async def run_health_check(
    agent: Agent,
    registry: ToolRegistry,
    memory: Memory,
    cluster_name: str,
    *,
    allow_skip: bool,
    full_interval: timedelta,
    ignore_alerts: tuple[str, ...] = (),
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> CheckResult:
    """A health check that skips the model when nothing changed since the last full check.

    Args:
        agent: A ready agent.
        registry: The session's tools (for the summary tools).
        memory: The agent's memory.
        cluster_name: For the report title.
        allow_skip: False for manual checks: always run the model.
        full_interval: Run the model at least this often even when nothing changed.
        ignore_alerts: Always-firing alert names left out of the comparison.
        clock: Current time (injectable for tests).
    """
    started_at = clock()
    t0 = time.monotonic()
    signature = await sweep_signature(registry, ignore_alerts)
    last_full = memory.get_meta(META_FULL_CHECK_AT)
    last_full_at = datetime.fromisoformat(last_full) if last_full else None
    if (
        allow_skip
        and signature is not None
        and signature == memory.get_meta(META_SWEEP_SIGNATURE)
        and last_full_at is not None
        and started_at - last_full_at < full_interval
    ):
        still_open = memory.open_findings()
        diff = FindingsDiff([], still_open, [])
        summary = (
            f"{SKIPPED_SUMMARY_PREFIX} the full check at {format_when(last_full_at)}: the same problems and "
            "firing alerts, so the model was not called."
        )
        report = render_check_report(cluster_name, started_at, summary, diff)
        duration = time.monotonic() - t0
        run_id = memory.record_run("check", started_at, duration, "ok", summary, report, 0, len(still_open), 0)
        logger.info("Health check skipped in %.0fs: unchanged since %s", duration, format_when(last_full_at))
        return CheckResult(True, summary, diff, report, run_id, started_at, duration)

    result = await run_check(agent, memory, cluster_name, clock=clock)
    if result.ok:
        # Only a successful full check is a baseline; after a failed one the next
        # run calls the model again.
        memory.set_meta(META_SWEEP_SIGNATURE, signature or "")
        memory.set_meta(META_FULL_CHECK_AT, started_at.isoformat())
    return result
