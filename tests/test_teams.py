import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from k8s_servant.findings import FindingsDiff, StoredFinding
from k8s_servant.healthcheck import CheckResult
from k8s_servant.report import MorningReport
from k8s_servant.scan.drift import ScanChanges
from k8s_servant.teams import (
    MAX_CARD_BYTES,
    check_card,
    morning_card,
    post_to_teams,
    resolve_webhook_url,
    should_post_check,
    should_post_report,
)

T0 = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)


def _stored(fingerprint, severity="warning", detail="details"):
    return StoredFinding(
        fingerprint, severity, f"title {fingerprint}", detail, T0 - timedelta(hours=2), T0, "open", None
    )


def _result(new=(), ongoing=(), resolved=(), ok=True, error=""):
    return CheckResult(
        ok, "Summary text", FindingsDiff(list(new), list(ongoing), list(resolved)), "# r", 1, T0, 3.0, error
    )


def _texts(payload):
    return " ".join(block.get("text", "") for block in payload["attachments"][0]["content"]["body"])


def _size(payload):
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def test_resolve_webhook_url(tmp_path):
    secret = tmp_path / "webhook-url"
    secret.write_text("https://from-file\n")
    assert (
        resolve_webhook_url({"teams_webhook_url_file": str(secret), "teams_webhook_url": "https://env"})
        == "https://from-file"
    )
    assert (
        resolve_webhook_url({"teams_webhook_url_file": str(tmp_path / "missing"), "teams_webhook_url": "https://env"})
        == ""
    )
    assert resolve_webhook_url({"teams_webhook_url_file": "", "teams_webhook_url": "https://env"}) == "https://env"


@pytest.mark.parametrize(
    "mode, new, resolved, ok, expected",
    [
        ("changes", 1, 0, True, True),
        ("changes", 0, 1, True, True),
        ("changes", 0, 0, True, False),
        ("changes", 1, 0, False, False),
        ("always", 0, 0, True, True),
        ("always", 0, 0, False, True),
        ("off", 1, 1, True, False),
    ],
)
def test_should_post_check(mode, new, resolved, ok, expected):
    result = _result(new=[_stored("n")] * new, resolved=[_stored("r")] * resolved, ok=ok)
    assert should_post_check(mode, result) is expected


def test_should_post_report():
    assert should_post_report("changes") and should_post_report("always") and not should_post_report("off")


def test_check_card_shape_and_content():
    payload = check_card(
        "my-cluster", _result(new=[_stored("a", "critical")], ongoing=[_stored("b")], resolved=[_stored("c")])
    )
    assert payload["type"] == "message"
    attachment = payload["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"]["type"] == "AdaptiveCard"
    texts = _texts(payload)
    assert "🔴 my-cluster: 1 new, 1 resolved, 2 open" in texts
    assert "Summary text" in texts and "title a" in texts and "✅ title c" in texts
    assert "title b (open 2 h 0 min)" in texts


def test_failed_check_card_shows_the_error():
    texts = _texts(check_card("my-cluster", _result(ok=False, error="no valid JSON")))
    assert "The health check failed: no valid JSON" in texts


def test_a_huge_card_is_shrunk_below_the_teams_limit():
    many = [_stored(f"crashloop/ns/deployment/app-{i}", "warning", "x" * 3000) for i in range(300)]
    payload = check_card("my-cluster", _result(new=many))
    assert _size(payload) <= MAX_CARD_BYTES
    assert "more" in _texts(payload)


def test_morning_card():
    report = MorningReport(
        generated_at=T0,
        local_day="Mon 12 Oct 2026",
        overview="All calm.",
        check_ok=True,
        check_error="",
        since=T0 - timedelta(hours=24),
        open_findings=[_stored("a", "critical")],
        new_open=[],
        resolved=[_stored("c")],
        came_and_went=[],
        checks_full=5,
        checks_skipped=19,
        checks_failed=0,
        alerts=[
            {"labels": {"alertname": "KubeNodeNotReady", "severity": "warning"}, "annotations": {}, "startsAt": "x"}
        ],
    )
    payload = morning_card("my-cluster", report)
    body = payload["attachments"][0]["content"]["body"]
    texts = _texts(payload)
    assert "☀️ Morning report — my-cluster — Mon 12 Oct 2026" in texts and "All calm." in texts
    assert "KubeNodeNotReady" in texts and "title a (open 2 h 0 min)" in texts
    facts = next(block for block in body if block["type"] == "FactSet")["facts"]
    assert {"title": "Health checks", "value": "24: 5 full, 19 unchanged (model skipped), 0 failed"} in facts
    assert _size(payload) <= MAX_CARD_BYTES


async def test_post_success():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx2.Response(202)

    payload = {"type": "message", "attachments": []}
    assert await post_to_teams("https://hook", payload, transport=httpx2.MockTransport(handler))
    assert seen == [payload]


async def test_post_http_error_returns_false():
    assert not await post_to_teams(
        "https://hook", {}, transport=httpx2.MockTransport(lambda r: httpx2.Response(400, text="bad card"))
    )


async def test_post_network_error_never_logs_the_url(caplog):
    def handler(request):
        raise httpx2.ConnectError("cannot connect to https://secret-signature-url", request=request)

    assert not await post_to_teams("https://secret-signature-url", {}, transport=httpx2.MockTransport(handler))
    assert "secret-signature-url" not in caplog.text
    assert "ConnectError" in caplog.text


def test_morning_card_shows_new_notes():
    report = MorningReport(
        generated_at=T0,
        local_day="Mon 12 Oct 2026",
        overview="All calm.",
        check_ok=True,
        check_error="",
        since=T0 - timedelta(hours=24),
        open_findings=[],
        new_open=[],
        resolved=[],
        came_and_went=[],
        checks_full=1,
        checks_skipped=0,
        checks_failed=0,
        alerts=None,
        new_notes=["- 2026-10-12: crash loops in payments-prod are expected"],
    )
    texts = _texts(morning_card("my-cluster", report))
    assert "Notes saved overnight" in texts and "payments-prod are expected" in texts


def _report(**overrides):
    fields = {
        "generated_at": T0,
        "local_day": "Mon 12 Oct 2026",
        "overview": "All calm.",
        "check_ok": True,
        "check_error": "",
        "since": T0 - timedelta(hours=24),
        "open_findings": [],
        "new_open": [],
        "resolved": [],
        "came_and_went": [],
        "checks_full": 1,
        "checks_skipped": 0,
        "checks_failed": 0,
        "alerts": None,
    }
    return MorningReport(**{**fields, **overrides})


def test_morning_card_shows_the_scan_changes():
    card = morning_card(
        "my-cluster",
        _report(
            scan_changes=ScanChanges(
                baseline=True,
                summary="First scan recorded: 3 components, 2 node pools",
                added=[],
                removed=[],
                changed=[],
                not_checked={},
            )
        ),
    )
    assert "First scan recorded: 3 components, 2 node pools" in json.dumps(card)


def test_scan_changes_do_not_break_the_size_limit():
    from k8s_servant.scan.facts import Fact

    added = [Fact(f"component/c{i}", "components", f"c{i} " + "x" * 400, (), "seen", "cluster", ()) for i in range(200)]
    card = morning_card("my-cluster", _report(scan_changes=ScanChanges(added=added)))
    assert _size(card) <= MAX_CARD_BYTES
