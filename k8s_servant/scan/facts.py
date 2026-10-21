"""Facts and scan results: what the initial scan learns, and how sure it is.

A *fact* is one thing known about a cluster, for example "Kyverno runs". Each
fact carries the evidence it was derived from and a *certainty*, so that only
proven facts are written as plain statements; `unsure` facts become questions
for a human. A fact also has a *scope*: `cluster` facts describe one cluster,
`platform` facts hold for every cluster of the platform.
"""

from dataclasses import dataclass, replace
from datetime import datetime

TOPICS = (
    "platform",
    "components",
    "mesh",
    "secrets",
    "monitoring",
    "network",
    "organisation",
    "noise",
)

# Headings of the knowledge sections, in the same order as TOPICS.
SECTION_TITLES = {
    "platform": "Platform",
    "components": "Components",
    "mesh": "Service mesh",
    "secrets": "Secret management",
    "monitoring": "Monitoring",
    "network": "Network",
    "organisation": "Organisation",
    "noise": "What is normal here",
}

# Most topics describe the platform as a whole; node pools and noise differ per cluster.
DEFAULT_SCOPE = {topic: "cluster" if topic in ("platform", "noise") else "platform" for topic in TOPICS}

# seen: found by the scan; unsure: needs a human; confirmed/told: a human said so.
CERTAINTIES = ("seen", "unsure", "confirmed", "told")


@dataclass(frozen=True)
class Fact:
    """One thing known about a cluster, with the evidence behind it."""

    key: str  # stable identity, e.g. component/kyverno
    topic: str  # one of TOPICS
    statement: str
    evidence: tuple[str, ...]
    certainty: str  # one of CERTAINTIES
    scope: str  # cluster | platform
    inputs: tuple[str, ...]  # which scan inputs it came from
    question: str = ""  # asked of a human when the fact is unsure
    setting: tuple[str, str] | None = None  # a suggested (ENV_VAR, value)
    setting_reason: str = ""

    def to_dict(self) -> dict:
        """Return a JSON-friendly dict."""
        return {
            "key": self.key,
            "topic": self.topic,
            "statement": self.statement,
            "evidence": list(self.evidence),
            "certainty": self.certainty,
            "scope": self.scope,
            "inputs": list(self.inputs),
            "question": self.question,
            "setting": list(self.setting) if self.setting else None,
            "setting_reason": self.setting_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Fact":
        """Rebuild a fact from `to_dict()` output (lists become tuples again)."""
        setting = data.get("setting")
        return cls(
            key=data["key"],
            topic=data["topic"],
            statement=data["statement"],
            evidence=tuple(data["evidence"]),
            certainty=data["certainty"],
            scope=data["scope"],
            inputs=tuple(data["inputs"]),
            question=data.get("question", ""),
            setting=(setting[0], setting[1]) if setting else None,
            setting_reason=data.get("setting_reason", ""),
        )


def fact(
    key: str,
    topic: str,
    statement: str,
    evidence: tuple[str, ...],
    inputs: tuple[str, ...],
    *,
    unsure_question: str = "",
    scope: str | None = None,
    setting: tuple[str, str] | None = None,
    setting_reason: str = "",
) -> Fact:
    """Build a fact: `unsure` when a question is given, otherwise `seen`."""
    return Fact(
        key=key,
        topic=topic,
        statement=statement,
        evidence=evidence,
        certainty="unsure" if unsure_question else "seen",
        scope=scope or DEFAULT_SCOPE[topic],
        inputs=inputs,
        question=unsure_question,
        setting=setting,
        setting_reason=setting_reason,
    )


@dataclass
class ScanResult:
    """Everything one scan produced."""

    scanned_at: datetime
    cluster: str
    facts: list[Fact]
    failed: dict[str, str]  # input name -> why it could not be read
    kubectl_ok: bool

    @property
    def ok(self) -> bool:
        """True when the cluster could be read at all."""
        return self.kubectl_ok


def apply_scope(facts: list[Fact], representative: bool) -> list[Fact]:
    """Pin every fact to this cluster unless the cluster represents the platform."""
    if representative:
        return facts
    return [replace(f, scope="cluster") for f in facts]
