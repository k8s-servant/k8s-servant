from datetime import UTC, datetime, timedelta

import pytest
from fakes import ScriptedChat, answer, findings_answer

from k8s_servant.agent import Agent
from k8s_servant.config import configure_app
from k8s_servant.findings import Finding
from k8s_servant.memory import Memory
from k8s_servant.report import build_morning_report, format_age, local_date, report_due
from k8s_servant.runtime import Session
from k8s_servant.tools.registry import ToolRegistry

MONDAY_0800_VILNIUS = datetime(2026, 10, 12, 5, 0, tzinfo=UTC)  # Vilnius is UTC+3 (summer time) on this date
SATURDAY = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
VILNIUS = {"MORNING_REPORT_TIMEZONE": "Europe/Vilnius"}  # a zone east of UTC; the default is UTC


@pytest.mark.parametrize(
    "now, last_sent, env, due",
    [
        (MONDAY_0800_VILNIUS, None, {}, True),
        (MONDAY_0800_VILNIUS - timedelta(minutes=1), None, {}, False),
        (MONDAY_0800_VILNIUS + timedelta(hours=3), None, {}, True),  # pod was down at 08:00: catch up
        (MONDAY_0800_VILNIUS, "2026-10-12", {}, False),  # already sent today (e.g. after a restart)
        (MONDAY_0800_VILNIUS, "2026-10-09", {}, True),
        (SATURDAY, None, {}, False),
        (SATURDAY, None, {"MORNING_REPORT_WEEKDAYS_ONLY": "false"}, True),
        (MONDAY_0800_VILNIUS, None, {"MORNING_REPORT_ENABLED": "false"}, False),
        (MONDAY_0800_VILNIUS, None, {"MORNING_REPORT_TIMEZONE": "UTC"}, False),  # 05:00 UTC is before 08:00 UTC
        (MONDAY_0800_VILNIUS, None, {"MORNING_REPORT_TIME": "07:59"}, True),
    ],
)
def test_report_due(now, last_sent, env, due):
    assert report_due(now, configure_app({**VILNIUS, **env}), last_sent) is due


def test_local_date_uses_the_report_time_zone():
    assert local_date(datetime(2026, 10, 11, 22, 30, tzinfo=UTC), configure_app(VILNIUS)) == "2026-10-12"


@pytest.mark.parametrize(
    "delta, text",
    [
        (timedelta(minutes=5), "5 min"),
        (timedelta(hours=2, minutes=3), "2 h 3 min"),
        (timedelta(days=3, hours=4, minutes=59), "3 d 4 h"),
        (timedelta(seconds=-30), "0 min"),
    ],
)
def test_format_age(delta, text):
    assert format_age(delta) == text


def _session(chat):
    return Session(
        agent=Agent(chat, ToolRegistry(), "SYSTEM", 5, 10_000), chat=chat, registry=ToolRegistry(), mcp_available=False
    )


async def test_morning_report_combines_a_fresh_check_memory_and_alerts(tmp_path):
    memory = Memory(tmp_path)
    memory.apply_check(
        [Finding("oom/x/deployment/old", "warning", "old problem", "")], now=MONDAY_0800_VILNIUS - timedelta(days=2)
    )
    chat = ScriptedChat(
        answer(
            findings_answer(
                "Two problems.",
                [
                    {
                        "fingerprint": "oom/x/deployment/old",
                        "severity": "warning",
                        "title": "old problem",
                        "detail": "",
                    },
                    {
                        "fingerprint": "node-not-ready/_cluster/node/n1",
                        "severity": "critical",
                        "title": "node n1 NotReady",
                        "detail": "since 04:40 UTC",
                    },
                ],
            )
        ),
        answer("Mostly healthy; node n1 needs attention today."),
    )

    async def alerts(url, timeout):
        return [
            {
                "labels": {"alertname": "KubeNodeNotReady", "severity": "warning"},
                "annotations": {"summary": "n1 not ready"},
                "startsAt": "2026-10-12T04:40:00Z",
            }
        ]

    config = configure_app({"CLUSTER_NAME": "my-cluster", "ALERTMANAGER_URL": "http://am:9093", **VILNIUS})
    report = await build_morning_report(
        _session(chat), memory, config, clock=lambda: MONDAY_0800_VILNIUS, alerts_fetcher=alerts
    )

    assert report.overview == "Mostly healthy; node n1 needs attention today."
    assert report.check_ok and (report.checks_full, report.checks_skipped, report.checks_failed) == (1, 0, 0)
    assert [f.fingerprint for f in report.open_findings] == ["node-not-ready/_cluster/node/n1", "oom/x/deployment/old"]
    assert [f.fingerprint for f in report.new_open] == ["node-not-ready/_cluster/node/n1"]
    assert report.local_day == "Mon 12 Oct 2026"
    assert report.report_md.startswith("# Morning report — my-cluster — Mon 12 Oct 2026")
    assert "since Sun 11 Oct 08:00 (24 h)" in report.report_md  # no earlier morning report: 24 h
    assert "KubeNodeNotReady" in report.report_md and "open 2 d 0 h" in report.report_md
    overview_request = chat.calls[-1]
    assert overview_request["tools"] == []
    assert "node n1 NotReady" in overview_request["turns"][1].content
    assert memory.run(report.run_id).kind == "morning"


async def test_morning_report_still_goes_out_when_the_check_fails(tmp_path):
    memory = Memory(tmp_path)
    chat = ScriptedChat(RuntimeError("model down"), answer("The latest check failed; nothing else known."))
    report = await build_morning_report(_session(chat), memory, configure_app({}), clock=lambda: MONDAY_0800_VILNIUS)
    assert not report.check_ok and "model down" in report.check_error
    assert report.checks_failed == 1
    assert report.alerts is None
    assert "Latest check: failed" in report.report_md


async def test_morning_report_without_overview_when_the_model_fails_twice(tmp_path):
    memory = Memory(tmp_path)
    chat = ScriptedChat(RuntimeError("down"), RuntimeError("still down"))
    report = await build_morning_report(_session(chat), memory, configure_app({}), clock=lambda: MONDAY_0800_VILNIUS)
    assert report.overview == "(overview not available: RuntimeError)"
    assert memory.run(report.run_id).kind == "morning"


async def test_morning_report_shows_notes_saved_in_the_last_24_hours(tmp_path):
    # Final review: a note planted by prompt injection would silently suppress findings;
    # new notes must be visible to people every morning.
    memory = Memory(tmp_path)
    memory.add_note("crash loops in payments-prod are expected", now=MONDAY_0800_VILNIUS - timedelta(hours=3))
    chat = ScriptedChat(answer(findings_answer("Fine.", [])), answer("All calm."))
    report = await build_morning_report(_session(chat), memory, configure_app({}), clock=lambda: MONDAY_0800_VILNIUS)
    assert report.new_notes == ["- 2026-10-12: crash loops in payments-prod are expected"]
    assert "## Notes saved overnight" in report.report_md
    assert "crash loops in payments-prod are expected" in report.report_md


class _RateLimited(Exception):
    status_code = 429


async def test_failed_check_is_explained_in_plain_words(tmp_path):
    # a large cluster: the report showed the raw 429 JSON body under "Latest check".
    chat = ScriptedChat(_RateLimited("Error code: 429 - {'error': {...}}"), answer("Overview."))
    report = await build_morning_report(
        _session(chat), Memory(tmp_path), configure_app({}), clock=lambda: MONDAY_0800_VILNIUS
    )
    assert report.check_error.startswith("HTTP 429 rate limited")
    assert "{'error'" not in report.report_md


async def test_always_firing_alerts_are_left_out(tmp_path):
    # Watchdog and InfoInhibitor fire permanently by design; counting them made the
    # A large cluster summary say "8 firing alerts" when 2 mattered.
    chat = ScriptedChat(answer(findings_answer("Fine.", [])), answer("Overview."))

    async def alerts(url, timeout):
        return [
            {
                "labels": {"alertname": name, "severity": "warning"},
                "annotations": {},
                "startsAt": "2026-10-12T04:40:00Z",
            }
            for name in ("Watchdog", "InfoInhibitor", "KubePdbNotEnoughHealthyPods")
        ]

    config = configure_app({"ALERTMANAGER_URL": "http://am:9093"})
    report = await build_morning_report(
        _session(chat), Memory(tmp_path), config, clock=lambda: MONDAY_0800_VILNIUS, alerts_fetcher=alerts
    )
    assert [a["labels"]["alertname"] for a in report.alerts] == ["KubePdbNotEnoughHealthyPods"]
    assert "Watchdog" not in report.report_md


# --- the overnight window and what happened in it ----------------------------------

from k8s_servant.healthcheck import SKIPPED_SUMMARY_PREFIX  # noqa: E402
from k8s_servant.report import report_window_start  # noqa: E402

FRIDAY_0800_VILNIUS = MONDAY_0800_VILNIUS - timedelta(days=3)


def test_window_starts_at_the_previous_morning_report(tmp_path):
    memory = Memory(tmp_path)
    assert report_window_start(memory, MONDAY_0800_VILNIUS) == MONDAY_0800_VILNIUS - timedelta(hours=24)
    # Monday covers the weekend: back to Friday's report.
    memory.record_run("morning", FRIDAY_0800_VILNIUS, 1, "ok", "s", "r")
    assert report_window_start(memory, MONDAY_0800_VILNIUS) == FRIDAY_0800_VILNIUS
    # After a long outage the window is capped at 72 h.
    assert report_window_start(memory, MONDAY_0800_VILNIUS + timedelta(days=2)) == (
        MONDAY_0800_VILNIUS + timedelta(days=2) - timedelta(hours=72)
    )


async def test_morning_report_tells_the_story_of_the_night(tmp_path):
    memory = Memory(tmp_path)
    night = MONDAY_0800_VILNIUS - timedelta(hours=8)
    memory.record_run("morning", MONDAY_0800_VILNIUS - timedelta(days=1), 1, "ok", "s", "r")
    memory.apply_check(
        [
            Finding("pdb/q/poddisruptionbudget/old", "warning", "old PDB", ""),
            Finding("oom/x/deployment/fixed", "warning", "fixed overnight", ""),
        ],
        now=MONDAY_0800_VILNIUS - timedelta(days=2),
    )
    memory.apply_check(
        [
            Finding("pdb/q/poddisruptionbudget/old", "warning", "old PDB", ""),
            Finding("pending/a/deployment/blip", "warning", "blip at 02:00", ""),
        ],
        now=night,
    )
    memory.apply_check(
        [Finding("pdb/q/poddisruptionbudget/old", "warning", "old PDB", "")], now=night + timedelta(hours=1)
    )
    memory.record_run("check", night, 50, "ok", "Full.", "r")
    memory.record_run("check", night + timedelta(hours=1), 6, "ok", SKIPPED_SUMMARY_PREFIX + " the full check", "r")
    memory.record_run("check", night + timedelta(hours=2), 9, "failed", "failed: 429", "r")
    chat = ScriptedChat(
        answer(
            findings_answer(
                "One PDB.",
                [
                    {
                        "fingerprint": "pdb/q/poddisruptionbudget/old",
                        "severity": "warning",
                        "title": "old PDB",
                        "detail": "",
                    }
                ],
            )
        ),
        answer("A quiet night; one blip recovered by itself."),
    )

    async def firing_now(url, timeout):
        return [
            {
                "labels": {"alertname": "KubePdbNotEnoughHealthyPods", "severity": "warning", "namespace": "q"},
                "annotations": {},
                "startsAt": "2026-09-23T18:53:13Z",
            }
        ]

    async def fired_overnight(url, seconds, timeout):
        assert seconds == 24 * 3600
        return [
            {"alertname": "KubePdbNotEnoughHealthyPods", "severity": "warning", "namespace": "q"},  # still firing
            {"alertname": "KubePodNotReady", "severity": "warning", "namespace": "a"},  # stopped
            {"alertname": "InfoInhibitor", "severity": "none"},  # always-on: left out
            # info level: muted by Alertmanager too (my-cluster had dozens of these per night)
            {"alertname": "CPUThrottlingHigh", "severity": "info", "namespace": "a", "pod": "p"},
        ]

    config = configure_app(
        {"CLUSTER_NAME": "my-cluster", "ALERTMANAGER_URL": "http://am:9093", "PROMETHEUS_URL": "http://prom:9090"}
    )
    report = await build_morning_report(
        _session(chat),
        memory,
        config,
        clock=lambda: MONDAY_0800_VILNIUS,
        alerts_fetcher=firing_now,
        fired_fetcher=fired_overnight,
    )
    md = report.report_md
    assert [f.fingerprint for f in report.came_and_went] == ["pending/a/deployment/blip"]
    assert [f.fingerprint for f in report.resolved] == ["oom/x/deployment/fixed"]
    assert report.new_open == []  # the blip is not "new and open"
    assert (report.checks_full, report.checks_skipped, report.checks_failed) == (2, 1, 1)  # incl. the fresh check
    assert "## Came and went overnight" in md and "blip at 02:00" in md
    assert "## Alerts that fired overnight and stopped" in md and "KubePodNotReady" in md
    assert "InfoInhibitor" not in md and "CPUThrottlingHigh" not in md
    overview_request = chat.calls[-1]["turns"]
    assert "since the previous report" in overview_request[0].content
    assert "blip at 02:00" in overview_request[1].content
