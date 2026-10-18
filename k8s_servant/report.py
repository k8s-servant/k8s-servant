"""The morning report: how the cluster did overnight and what needs attention today.

Hourly checks post only when something changes. Every (week)day morning the
administrators also get the story of the night, posted even when nothing changed.
It covers the time since the previous morning report, so Monday's report covers
the weekend (at most 72 hours; 24 hours when there was no earlier report):

  - a fresh health check first, so the report reflects the cluster right now;
  - from memory: open findings and how long each has been open; what appeared and
    is still open, what was resolved, and what came and went during the night;
    how many checks ran in full, were skipped as unchanged, or failed;
  - alerts firing now (Alertmanager) and alerts that fired overnight but have
    stopped (Prometheus' ALERTS history), without the always-firing ones;
  - a short overview in plain words, written by the model from these facts only:
    how the night went and what to keep an eye on today.

If the check or the model fails, the report still goes out with what memory
knows, saying what failed: a silent morning would look like "all is fine".

When it is due (report_due): at MORNING_REPORT_TIME in MORNING_REPORT_TIMEZONE, on
weekdays only when MORNING_REPORT_WEEKDAYS_ONLY. The scheduler stores the date of
the last report in memory (LAST_REPORT_KEY), so a restart never sends the same
day's report twice, and a pod that was down at 08:00 sends it as soon as it is
back the same day.
"""

import logging
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from k8s_servant.findings import StoredFinding
from k8s_servant.healthcheck import SKIPPED_SUMMARY_PREFIX, record_failed_run, run_check
from k8s_servant.llm import model_error_text
from k8s_servant.llm.types import Turn
from k8s_servant.memory import Memory
from k8s_servant.tools.prometheus_tool import fetch_alerts_fired_since, fetch_firing_alerts, format_alerts

logger = logging.getLogger(__name__)

LAST_REPORT_KEY = "last_morning_report_date"

# The window: since the previous morning report, but never more than this.
DEFAULT_WINDOW = timedelta(hours=24)
MAX_WINDOW = timedelta(hours=72)

OVERVIEW_PROMPT = (
    "You write the morning report of a Kubernetes cluster for its administrators. It covers the "
    "time since the previous report: usually the night, on Mondays the weekend. In 3 to 6 short "
    "sentences of plain language, say how the cluster did during that time (quiet or eventful), "
    "what needs attention today (most important first), and anything that recovered by itself "
    "but is worth a look or keeps coming back. Use only the facts given; do not invent anything. "
    "No headings, no lists."
)


def format_age(delta: timedelta) -> str:
    """'3 d 4 h', '2 h 3 min' or '5 min'."""
    minutes = max(0, int(delta.total_seconds() // 60))
    days, rest = divmod(minutes, 24 * 60)
    hours, mins = divmod(rest, 60)
    if days:
        return f"{days} d {hours} h"
    if hours:
        return f"{hours} h {mins} min"
    return f"{mins} min"


def _local(now: datetime, config: dict) -> datetime:
    """Convert a UTC time to the morning report's time zone."""
    return now.astimezone(ZoneInfo(config["morning_report_timezone"]))


def local_date(now: datetime, config: dict) -> str:
    """Today's date (ISO) in the report's time zone."""
    return _local(now, config).date().isoformat()


def report_window_start(memory: Memory, now: datetime) -> datetime:
    """Where the report's window starts: the previous successful morning report.

    Monday's report therefore covers the weekend. Capped at MAX_WINDOW (a long
    outage); DEFAULT_WINDOW when there is no earlier report.
    """
    previous = memory.last_run("morning")
    if previous is None:
        return now - DEFAULT_WINDOW
    return max(previous.started_at, now - MAX_WINDOW)


def _alert_where(labels: dict) -> str:
    """Where an alert points to, from its labels: `namespace=... pod=... node=... job=...`."""
    return " ".join(f"{key}={labels[key]}" for key in ("namespace", "pod", "node", "job") if labels.get(key))


def report_due(now: datetime, config: dict, last_sent_date: str | None) -> bool:
    """Whether the morning report should be produced now.

    Args:
        now: Current time (UTC).
        config: Settings (morning_report_*).
        last_sent_date: ISO date of the last report in the report's time zone,
            from memory; None if never sent.
    """
    if not config["morning_report_enabled"]:
        return False
    local = _local(now, config)
    if config["morning_report_weekdays_only"] and local.weekday() >= 5:
        return False
    hour, minute = (int(part) for part in config["morning_report_time"].split(":"))
    if (local.hour, local.minute) < (hour, minute):
        return False
    return last_sent_date != local.date().isoformat()


@dataclass
class MorningReport:
    """Everything in one morning report."""

    generated_at: datetime
    since: datetime  # start of the window (the previous morning report)
    local_day: str  # e.g. "Mon 12 Oct 2026"
    overview: str
    check_ok: bool
    check_error: str
    open_findings: list[StoredFinding]
    new_open: list[StoredFinding]  # appeared in the window and still open
    resolved: list[StoredFinding]  # open before the window, resolved in it
    came_and_went: list[StoredFinding]  # appeared and resolved within the window
    checks_full: int
    checks_skipped: int  # unchanged, the model was not called
    checks_failed: int
    alerts: list[dict] | None  # firing now; None: Alertmanager not configured or not reachable
    # Fired in the window but no longer firing (label dicts); None: no Prometheus.
    alerts_stopped: list[dict] | None = None
    report_md: str = ""
    run_id: int = 0
    # Learned notes saved in the window. Shown to people because a note changes
    # what every future run treats as normal.
    new_notes: list[str] = field(default_factory=list)
    timezone: str = "UTC"

    def window_text(self) -> str:
        """e.g. 'since Sun 11 Oct 08:00 (24 h)' in the report's time zone."""
        start = self.since.astimezone(ZoneInfo(self.timezone))
        hours = round((self.generated_at - self.since).total_seconds() / 3600)
        return f"since {start:%a %d %b %H:%M} ({hours} h)"

    def facts(self) -> list[tuple[str, str]]:
        """The 'at a glance' lines (Markdown report and Teams FactSet)."""
        by_severity = Counter(f.severity for f in self.open_findings)
        checks = self.checks_full + self.checks_skipped + self.checks_failed
        return [
            ("Covers", self.window_text()),
            (
                "Open findings",
                f"{len(self.open_findings)} (critical {by_severity['critical']}, "
                f"warning {by_severity['warning']}, info {by_severity['info']})",
            ),
            (
                "Overnight",
                f"{len(self.new_open)} new, {len(self.resolved)} resolved, {len(self.came_and_went)} came and went",
            ),
            (
                "Health checks",
                f"{checks}: {self.checks_full} full, {self.checks_skipped} unchanged "
                f"(model skipped), {self.checks_failed} failed",
            ),
            ("Latest check", "ok" if self.check_ok else f"failed: {self.check_error}"),
            ("Alerts firing now", "not available" if self.alerts is None else str(len(self.alerts))),
            (
                "Alerts that stopped overnight",
                "not available" if self.alerts_stopped is None else str(len(self.alerts_stopped)),
            ),
        ]

    def alert_lines(self) -> list[str]:
        """Alerts firing now, one formatted line each (empty when there are none or unknown)."""
        return format_alerts(self.alerts).splitlines() if self.alerts else []

    def stopped_alert_lines(self) -> list[str]:
        """Alerts that fired in the window and stopped, one line each: name, severity, location."""
        return [
            f"{a.get('alertname', '?')} [{a.get('severity', 'none')}] {_alert_where(a)}".strip()
            for a in self.alerts_stopped or []
        ]


def render_facts_md(report: MorningReport) -> str:
    """The factual part of the report, in Markdown (also what the model summarises)."""
    sections = ["## At a glance\n\n" + "\n".join(f"- {title}: {value}" for title, value in report.facts())]

    def listing(title: str, lines: list[str]) -> None:
        """Add a Markdown section with a bullet per line; skipped when there are no lines."""
        if lines:
            sections.append(f"## {title}\n\n" + "\n".join(f"- {line}" for line in lines))

    listing(
        "Open findings",
        [
            f"[{f.severity}] {f.title} — open {format_age(report.generated_at - f.first_seen)} — `{f.fingerprint}`"
            for f in report.open_findings
        ],
    )
    listing("New overnight, still open", [f"[{f.severity}] {f.title} — `{f.fingerprint}`" for f in report.new_open])
    listing("Resolved overnight", [f"{f.title} — `{f.fingerprint}`" for f in report.resolved])
    listing(
        "Came and went overnight",
        [
            f"[{f.severity}] {f.title} — from {f.first_seen:%H:%M} to {f.resolved_at:%H:%M} UTC — `{f.fingerprint}`"
            for f in report.came_and_went
        ],
    )
    listing("Alerts firing now", report.alert_lines())
    listing("Alerts that fired overnight and stopped", report.stopped_alert_lines())
    if report.new_notes:
        sections.append(
            "## Notes saved overnight\n\n"
            "The agent will treat these as facts in every future run; remove any that are wrong "
            "(see docs/memory.md).\n\n" + "\n".join(report.new_notes)
        )
    return "\n\n".join(sections)


async def build_morning_report(
    session,
    memory: Memory,
    config: dict,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    alerts_fetcher=fetch_firing_alerts,
    fired_fetcher=fetch_alerts_fired_since,
) -> MorningReport:
    """Run a fresh check, gather the night from memory and Prometheus, and write the report.

    Args:
        session: runtime.Session (uses .agent for the check, .chat for the overview).
        memory: The agent's memory.
        config: Settings.
        clock: Current time (injectable for tests).
        alerts_fetcher: Function (url, timeout) -> list of alerts firing now (injectable).
        fired_fetcher: Function (url, seconds, timeout) -> alerts fired in the window (injectable).

    Returns:
        The report, already stored as a run of kind "morning".
    """
    started = clock()
    since = report_window_start(memory, started)
    t0 = time.monotonic()
    cluster = config["cluster_name"]

    # 1. A fresh check, so the summary reflects the cluster right now.
    check_ok, check_error = False, ""
    try:
        check = await run_check(session.agent, memory, cluster, clock=clock)
        check_ok, check_error = check.ok, check.error
    except Exception as exc:  # the report must still go out when the model or a tool fails
        check_error = model_error_text(exc, config)
        logger.error("Health check for the morning report failed: %s", check_error)
        record_failed_run(memory, "check", started, check_error)

    # 2. The night from memory.
    appeared, resolved = memory.findings_changed_since(since)
    came_and_went = [f for f in appeared if f.status == "resolved"]
    checks = [run for run in memory.runs_since(since) if run.kind == "check"]
    skipped = sum(1 for run in checks if run.status == "ok" and run.summary.startswith(SKIPPED_SUMMARY_PREFIX))
    failed = sum(1 for run in checks if run.status != "ok")

    # 3. Firing alerts, when Alertmanager is configured.
    ignored = set(config["alerts_ignore_names"])
    alerts = None
    if config["alertmanager_url"]:
        try:
            alerts = await alerts_fetcher(config["alertmanager_url"], config["prometheus_timeout_seconds"])
            alerts = [a for a in alerts if a.get("labels", {}).get("alertname") not in ignored]
        except Exception as exc:
            logger.warning("Could not read Alertmanager for the morning report: %s", type(exc).__name__)
    stopped = None
    if config["prometheus_url"]:
        try:
            fired = await fired_fetcher(
                config["prometheus_url"], int((started - since).total_seconds()), config["prometheus_timeout_seconds"]
            )
            now_firing = {
                (a.get("labels", {}).get("alertname"), a.get("labels", {}).get("namespace")) for a in alerts or []
            }
            # Info-level alerts are left out: Alertmanager mutes them (InfoInhibitor), and
            # A large cluster fires dozens per night (CPU throttling per pod).
            stopped = [
                a
                for a in fired
                if a.get("alertname") not in ignored
                and a.get("severity") not in ("info", "none")
                and (a.get("alertname"), a.get("namespace")) not in now_firing
            ]
        except Exception as exc:
            logger.warning("Could not read the alert history from Prometheus: %s", type(exc).__name__)

    report = MorningReport(
        generated_at=started,
        local_day=_local(started, config).strftime("%a %d %b %Y"),
        overview="",
        check_ok=check_ok,
        check_error=check_error,
        open_findings=memory.open_findings(),
        since=since,
        new_open=[f for f in appeared if f.status == "open"],
        resolved=[f for f in resolved if f.first_seen < since],
        came_and_went=came_and_went,
        checks_full=len(checks) - skipped - failed,
        checks_skipped=skipped,
        checks_failed=failed,
        alerts=alerts,
        alerts_stopped=stopped,
        timezone=config["morning_report_timezone"],
        new_notes=memory.notes_since(since),
    )
    facts_md = render_facts_md(report)

    # 4. The overview in plain words, from these facts only (no tools).
    try:
        reply = await session.chat.complete(
            [Turn("system", OVERVIEW_PROMPT), Turn("user", f"Cluster: {cluster}\n\n{facts_md}")],
            [],
        )
        report.overview = reply.content.strip() or "(no overview)"
    except Exception as exc:
        logger.error("Could not write the morning overview: %s", type(exc).__name__)
        report.overview = f"(overview not available: {type(exc).__name__})"

    report.report_md = (
        f"# Morning report — {cluster} — {report.local_day}\n\n_Covers the time "
        f"{report.window_text()}._\n\n{report.overview}\n\n{facts_md}\n"
    )
    report.run_id = memory.record_run(
        "morning",
        started,
        time.monotonic() - t0,
        "ok",
        report.overview[:500],
        report.report_md,
        len(report.new_open),
        len(report.open_findings),
        len(report.resolved),
    )
    return report
