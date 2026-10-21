"""Posting to MS Teams.

Teams receives the messages through a **Workflows** (Power Automate) webhook: a
URL that accepts an HTTP POST with an Adaptive Card and posts it to a channel.
(The older Office 365 "Incoming Webhook" connectors were retired in 2026.) The
URL contains a signature that grants posting rights, so it is a credential: it
comes from a mounted Kubernetes Secret file (TEAMS_WEBHOOK_URL_FILE) in the
cluster, or TEAMS_WEBHOOK_URL locally, and it is never logged.

Two cards:
  check_card()   after a health check: new, resolved and still-open findings
  morning_card()   the morning summary

Teams rejects messages larger than about 28 KB, so _fit() shrinks a card step by
step (shorter details, no details, fewer items) until it fits.

When to post is decided by TEAMS_POST_MODE:
  changes  (default) post a check only when something is new or resolved
  always   post every check, including failed ones
  off      never post (reports still go to the logs and `k8s-servant history`)
The morning report is posted unless the mode is off.
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path

import httpx2

from k8s_servant.findings import StoredFinding
from k8s_servant.healthcheck import CheckResult, format_when
from k8s_servant.report import MorningReport, format_age

logger = logging.getLogger(__name__)

# Teams' limit is ~28 KB for the whole message; keep a safety margin.
MAX_CARD_BYTES = 27_000

_ICON = {"critical": "🔴", "warning": "🟠", "info": "🔵"}

# Shrinking steps: (max characters per detail: None = full, 0 = none; max items per list: None = all)
_SHRINK_STEPS = [(None, None), (300, None), (0, None), (0, 20), (0, 5)]


def resolve_webhook_url(config: dict) -> str:
    """The webhook URL: from TEAMS_WEBHOOK_URL_FILE when set, else TEAMS_WEBHOOK_URL ('' if none)."""
    path = config["teams_webhook_url_file"]
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError as exc:
            logger.warning("Cannot read TEAMS_WEBHOOK_URL_FILE: %s", exc.strerror)
            return ""
    return config["teams_webhook_url"]


def should_post_check(mode: str, result: CheckResult) -> bool:
    """Post a health check? (changes: only when something is new or resolved)."""
    if mode == "always":
        return True
    return mode == "changes" and result.ok and result.diff.changed


def should_post_report(mode: str) -> bool:
    """Post the morning report? (always, unless posting is off)."""
    return mode != "off"


def _cut(text: str, limit: int) -> str:
    """Shorten text to `limit` characters, ending with an ellipsis when cut."""
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _text(text: str, **style) -> dict:
    """An Adaptive Card TextBlock (Markdown subset, wrapped)."""
    return {"type": "TextBlock", "text": text, "wrap": True, **style}


def _items(
    findings: list[StoredFinding], detail_chars: int | None, max_items: int | None, line: Callable[[StoredFinding], str]
) -> list[dict]:
    """One TextBlock per finding (plus an optional detail line), capped in number."""
    shown = findings if max_items is None else findings[:max_items]
    blocks: list[dict] = []
    for finding in shown:
        blocks.append(_text(line(finding)))
        if detail_chars != 0 and finding.detail:
            detail = finding.detail if detail_chars is None else _cut(finding.detail, detail_chars)
            blocks.append(_text(detail, isSubtle=True, size="Small", spacing="None"))
    if len(findings) > len(shown):
        blocks.append(_text(f"… and {len(findings) - len(shown)} more (see `k8s-servant findings`)", isSubtle=True))
    return blocks


def _section(title: str, blocks: list[dict]) -> list[dict]:
    """A titled card section; nothing at all when it has no blocks."""
    return [_text(title, weight="Bolder", spacing="Medium", separator=True), *blocks] if blocks else []


def _icon(severity: str) -> str:
    """Coloured dot for a finding's severity."""
    return _ICON.get(severity, "•")


def _headline_icon(open_findings: list[StoredFinding]) -> str:
    """The card headline's icon: red with a critical finding, orange with any finding, green otherwise."""
    severities = {f.severity for f in open_findings}
    return "🔴" if "critical" in severities else "🟠" if severities else "✅"


def _message(body: list[dict]) -> dict:
    """Wrap card body elements in the message format the Workflows webhook expects."""
    return {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "contentUrl": None,
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "msteams": {"width": "Full"},
                    "body": body,
                },
            }
        ],
    }


def _fit(build: Callable[[int | None, int | None], list[dict]]) -> dict:
    """Build the card with ever smaller content until it fits MAX_CARD_BYTES."""
    payload: dict = {}
    for detail_chars, max_items in _SHRINK_STEPS:
        payload = _message(build(detail_chars, max_items))
        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        if size <= MAX_CARD_BYTES:
            return payload
        logger.info("Teams card is %d bytes (limit %d); shrinking", size, MAX_CARD_BYTES)
    return payload


def check_card(cluster_name: str, result: CheckResult) -> dict:
    """The card posted after a health check."""
    diff = result.diff

    def build(detail_chars, max_items):
        """The card body; _fit() calls it with smaller limits until the card fits."""
        body = [
            _text(
                f"{_headline_icon(diff.new + diff.ongoing)} {cluster_name}: {len(diff.new)} new, "
                f"{len(diff.resolved)} resolved, {diff.open_count} open",
                size="Medium",
                weight="Bolder",
            ),
            _text(f"Health check {format_when(result.started_at)}", isSubtle=True, spacing="None"),
        ]
        if not result.ok:
            body.append(_text(f"⚠️ The health check failed: {_cut(result.error, 500)}"))
        elif result.summary:
            body.append(_text(_cut(result.summary, 2000)))
        body += _section(
            "New", _items(diff.new, detail_chars, max_items, lambda f: f"{_icon(f.severity)} **{_cut(f.title, 200)}**")
        )
        body += _section("Resolved", _items(diff.resolved, 0, max_items, lambda f: f"✅ {_cut(f.title, 200)}"))
        body += _section(
            "Still open",
            _items(
                diff.ongoing,
                0,
                max_items,
                lambda f: (
                    f"{_icon(f.severity)} {_cut(f.title, 200)} (open {format_age(result.started_at - f.first_seen)})"
                ),
            ),
        )
        return body

    return _fit(build)


def morning_card(cluster_name: str, report: MorningReport) -> dict:
    """The card for the morning report."""

    def build(detail_chars, max_items):
        """The card body; _fit() calls it with smaller limits until the card fits."""
        body = [
            _text(f"☀️ Morning report — {cluster_name} — {report.local_day}", size="Medium", weight="Bolder"),
            _text(_cut(report.overview, 3000)),
            {"type": "FactSet", "facts": [{"title": title, "value": value} for title, value in report.facts()]},
        ]
        body += _section(
            "Open findings",
            _items(
                report.open_findings,
                detail_chars,
                max_items,
                lambda f: (
                    f"{_icon(f.severity)} {_cut(f.title, 200)} (open {format_age(report.generated_at - f.first_seen)})"
                ),
            ),
        )
        body += _section(
            "New overnight, still open",
            _items(report.new_open, 0, max_items, lambda f: f"{_icon(f.severity)} {_cut(f.title, 200)}"),
        )
        body += _section(
            "Resolved overnight", _items(report.resolved, 0, max_items, lambda f: f"✅ {_cut(f.title, 200)}")
        )
        body += _section(
            "Came and went overnight",
            _items(
                report.came_and_went,
                0,
                max_items,
                lambda f: f"↩️ {_cut(f.title, 200)} ({f.first_seen:%H:%M}–{f.resolved_at:%H:%M} UTC)",  # noqa: RUF001 (a real en dash for a time range)
            ),
        )
        limit = max_items or 15
        if report.scan_changes is not None:
            lines = report.scan_changes.lines()
            shown = [_text(_cut(line, 300)) for line in lines[:limit]]
            if len(lines) > limit:
                shown.append(_text(f"… and {len(lines) - limit} more (see the report)", isSubtle=True))
            body += _section("Since the last scan", shown)
        body += _section("Alerts firing now", [_text(_cut(line, 300)) for line in report.alert_lines()[:limit]])
        body += _section(
            "Alerts that fired overnight and stopped",
            [_text(_cut(line, 300)) for line in report.stopped_alert_lines()[:limit]],
        )
        notes = [_text(_cut(note, 300)) for note in report.new_notes[:limit]]
        if notes:
            notes.insert(0, _text("The agent will treat these as facts from now on; remove wrong ones.", isSubtle=True))
        body += _section("Notes saved overnight", notes)
        return body

    return _fit(build)


async def post_to_teams(url: str, payload: dict, timeout: float = 30, transport=None) -> bool:
    """POST a card to the Workflows webhook. Returns True on success.

    Failures are logged without the URL (it is a credential) and never raised:
    a Teams outage must not fail a health check.

    Args:
        url: The webhook URL (resolve_webhook_url()).
        payload: A message built by check_card() or morning_card().
        timeout: Seconds for the whole request.
        transport: httpx2 transport; tests pass a MockTransport.
    """
    try:
        async with httpx2.AsyncClient(timeout=timeout, transport=transport) as client:
            response = await client.post(url, json=payload)
    except httpx2.HTTPError as exc:
        logger.error("Posting to Teams failed: %s (network error; URL not shown)", type(exc).__name__)
        return False
    if response.status_code >= 300:
        logger.error("Posting to Teams failed: HTTP %d %s", response.status_code, response.text[:200])
        return False
    logger.info("Posted to Teams (HTTP %d)", response.status_code)
    return True
