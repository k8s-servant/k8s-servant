"""Findings: what a health check reports, and how two checks are compared.

A *finding* is one problem the agent found, for example "Deployment api in
payments-dev is crash-looping". Each finding has a **fingerprint**: a stable
identity built from *what* is broken, not *when* or *how* it was seen:

    <category>/<namespace, or _cluster>/<Kind>/<name>
    e.g. crashloop/payments-dev/deployment/api

The fingerprint is what makes memory work. When the next check reports the same
fingerprint, the problem is "still open" instead of "new", so Teams is not
flooded with the same issue every hour. Fingerprints are normalised (lower case,
no spaces) because the model will not always spell them identically.

The model returns its findings as a JSON block at the end of its answer (the
format is defined in healthcheck.CHECK_PROMPT); parse_findings() extracts and
validates it.
"""

import json
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

SEVERITIES = ("critical", "warning", "info")
_SEVERITY_RANK = {severity: rank for rank, severity in enumerate(SEVERITIES)}

# A fenced ```json block; the last one in the answer is used.
_JSON_BLOCK = re.compile(r"```json[ \t]*\n(.*?)\n[ \t]*```", re.DOTALL)


class FindingsParseError(ValueError):
    """The model's answer did not contain a valid findings JSON block."""


@dataclass(frozen=True)
class Finding:
    """One problem reported by a health check."""

    fingerprint: str
    severity: str  # critical | warning | info
    title: str
    detail: str


@dataclass(frozen=True)
class StoredFinding(Finding):
    """A finding as kept in memory, with its history."""

    first_seen: datetime
    last_seen: datetime
    status: str  # open | resolved
    resolved_at: datetime | None


@dataclass
class FindingsDiff:
    """How the current check compares with the findings that were open before it."""

    new: list[StoredFinding]
    ongoing: list[StoredFinding]
    resolved: list[StoredFinding]

    @property
    def open_count(self) -> int:
        """Problems open after this check (new plus still open)."""
        return len(self.new) + len(self.ongoing)

    @property
    def changed(self) -> bool:
        """True when something appeared or disappeared since the previous check."""
        return bool(self.new or self.resolved)


def normalize_fingerprint(raw) -> str:
    """Lower-case each '/'-separated part, join words inside a part with '-', drop empty parts.

    "CrashLoop / Payments-Dev // Deployment / API" -> "crashloop/payments-dev/deployment/api".
    Each part is cleaned on its own, so spaces around the slashes never change the result.
    """
    parts = ("-".join(part.split()) for part in str(raw or "").lower().split("/"))
    return "/".join(part for part in parts if part)


def sort_findings(findings: Iterable) -> list:
    """Most severe first, then alphabetically by fingerprint (stable output)."""
    return sorted(findings, key=lambda f: (_SEVERITY_RANK.get(f.severity, len(SEVERITIES)), f.fingerprint))


def parse_findings(answer: str) -> tuple[str, list[Finding]]:
    """Extract the summary and findings from the model's final answer.

    Args:
        answer: The model's answer; it should end with a ```json block.

    Returns:
        (summary, findings). Duplicate fingerprints are merged, keeping the most
        severe one. An unknown severity becomes "warning".

    Raises:
        FindingsParseError: If there is no valid findings JSON.
    """
    blocks = _JSON_BLOCK.findall(answer or "")
    candidate = blocks[-1] if blocks else (answer or "").strip()
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        raise FindingsParseError("the answer does not end with a valid ```json block") from None
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        raise FindingsParseError("the JSON block must be an object with a 'findings' list")
    summary = str(data.get("summary") or "").strip()
    by_fingerprint: dict[str, Finding] = {}
    for index, item in enumerate(data["findings"]):
        if not isinstance(item, dict):
            raise FindingsParseError(f"finding #{index + 1} is not a JSON object")
        fingerprint = normalize_fingerprint(item.get("fingerprint"))
        if not fingerprint:
            raise FindingsParseError(f"finding #{index + 1} has no fingerprint")
        severity = str(item.get("severity") or "").strip().lower()
        if severity not in SEVERITIES:
            logger.warning("Finding %s has unknown severity %r; using 'warning'", fingerprint, severity)
            severity = "warning"
        finding = Finding(
            fingerprint=fingerprint,
            severity=severity,
            title=str(item.get("title") or fingerprint).strip(),
            detail=str(item.get("detail") or "").strip(),
        )
        existing = by_fingerprint.get(fingerprint)
        if existing is None or _SEVERITY_RANK[severity] < _SEVERITY_RANK[existing.severity]:
            by_fingerprint[fingerprint] = finding
    return summary, list(by_fingerprint.values())
