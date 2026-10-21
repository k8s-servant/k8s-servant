"""Stored scans and the comparison between two of them.

Why: the in-cluster agent re-scans on a schedule and tells the humans what moved
since last time, so the knowledge they maintain does not rot. The previous scan is
kept in the memory database. A fact whose input could not be read this time is left
exactly as it was, so a forbidden or unreachable input never looks like something
disappearing; the same holds for removals when a whole detector crashed.
"""

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from k8s_servant.memory import Memory
from k8s_servant.scan.facts import CERTAINTIES, TOPICS, Fact, ScanResult
from k8s_servant.scan.run import scan
from k8s_servant.tools.cluster_tools import Fetch

log = logging.getLogger(__name__)

LAST_SCAN_KEY = "last_cluster_scan"
_VERSION = 1
_SCOPES = ("platform", "cluster")
# Monitoring that is simply not there is normal on many clusters and never reported.
_NOISE_ONLY = frozenset({"prometheus", "alertmanager"})


def save_scan(memory: Memory, result: ScanResult) -> None:
    """Store a scan result as the one to compare the next scan with."""
    memory.set_meta(
        LAST_SCAN_KEY,
        json.dumps(
            {
                "version": _VERSION,
                "scanned_at": result.scanned_at.isoformat(),
                "cluster": result.cluster,
                "facts": [f.to_dict() for f in result.facts],
                "failed": result.failed,
            }
        ),
    )


def load_scan(memory: Memory) -> ScanResult | None:
    """Read the stored scan; None when there is none or it cannot be trusted.

    Only what was wrong is logged, never the stored content.
    """
    raw = memory.get_meta(LAST_SCAN_KEY)
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        log.warning("The stored scan is not JSON; ignoring it")
        return None
    if not isinstance(data, dict) or data.get("version") != _VERSION:
        log.warning("The stored scan has an unknown version; ignoring it")
        return None
    try:
        facts = [Fact.from_dict(item) for item in data["facts"]]
        scanned_at = datetime.fromisoformat(data["scanned_at"])
        cluster, failed = data["cluster"], data["failed"]
        if not isinstance(cluster, str) or not isinstance(failed, dict):
            raise TypeError("cluster or failed has the wrong type")
        if scanned_at.tzinfo is None:
            raise ValueError("scanned_at has no timezone")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in failed.items()):
            raise TypeError("failed holds a non-string")
        if not all(isinstance(f.key, str) and isinstance(f.statement, str) for f in facts):
            raise TypeError("a fact has a non-string key or statement")
    except (KeyError, TypeError, ValueError, IndexError, AttributeError):
        log.warning("The stored scan has a malformed field; ignoring it")
        return None
    if any(f.topic not in TOPICS or f.certainty not in CERTAINTIES or f.scope not in _SCOPES for f in facts):
        log.warning("The stored scan holds a fact with an unknown topic, certainty or scope; ignoring it")
        return None
    return ScanResult(scanned_at, cluster, facts, failed, True)


@dataclass
class ScanChanges:
    """What differs between the previous scan and this one."""

    baseline: bool = False
    added: list[Fact] = field(default_factory=list)
    removed: list[Fact] = field(default_factory=list)
    changed: list[tuple[Fact, Fact]] = field(default_factory=list)  # (old, new)
    not_checked: dict[str, str] = field(default_factory=dict)
    error: str = ""
    summary: str = ""  # the baseline text

    def headline(self) -> str:
        """One line saying what happened."""
        if self.error:
            return f"failed: {self.error[:300]}"
        if self.baseline:
            return self.summary
        if not (self.added or self.removed or self.changed):
            return "no changes"
        return f"{len(self.added)} added, {len(self.removed)} removed, {len(self.changed)} changed"

    def lines(self) -> list[str]:
        """One line per change, saying where in the knowledge to apply it."""
        out = []
        for fact in self.added:
            line = f"Added: {fact.statement} — add to knowledge.{_target(fact)}: `- {fact.statement}`"
            if fact.setting:
                line += f" and appConfig.{fact.setting[0]}: {fact.setting[1]}"
            out.append(line)
        out += [f"Gone: {f.statement} — remove it from knowledge.{_target(f)}" for f in self.removed]
        out += [f"Changed: {old.statement} → {new.statement}" for old, new in self.changed]
        if self.not_checked:
            out.append("Not checked this time: " + ", ".join(sorted(self.not_checked)))
        return out


def _target(fact: Fact) -> str:
    return "environment" if fact.scope == "platform" else "clusterNotes"


def compare(old: ScanResult | None, new: ScanResult) -> tuple[ScanChanges, ScanResult]:
    """Compare two scans; return the changes and the result to store.

    Noise is never compared (the new noise replaces the old). Facts that came from
    an input that failed this time stay as they were. When a detector crashed,
    missing facts are kept rather than reported gone, so a real removal waits for
    the next clean scan.
    """
    failed = set(new.failed)
    detector_crashed = any(key.startswith("detector:") for key in failed)

    def untouched(fact: Fact) -> bool:
        return bool(failed.intersection(fact.inputs))

    noise = [f for f in new.facts if f.topic == "noise"]
    new_facts = {f.key: f for f in new.facts if f.topic != "noise" and not untouched(f)}
    # A failure is reported once, when new since the stored scan (all on a baseline); a crashed detector every time.
    previous = set(old.failed) if old else set()
    changes = ScanChanges(
        not_checked={
            k: v
            for k, v in new.failed.items()
            if k.startswith("detector:") or (k not in _NOISE_ONLY and k not in previous)
        }
    )
    stored = dict(new_facts)
    if old is None:
        components = sum(k.startswith("component/") for k in new_facts)
        pools = sum(k.startswith("nodepool/") for k in new_facts)
        changes.baseline = True
        changes.summary = f"First scan recorded: {components} components, {pools} node pools"
    else:
        old_facts = {f.key: f for f in old.facts if f.topic != "noise"}
        for key, fact in old_facts.items():
            if key in new_facts:
                if fact.statement != new_facts[key].statement:
                    changes.changed.append((fact, new_facts[key]))
            elif untouched(fact) or detector_crashed:
                stored[key] = fact
            else:
                changes.removed.append(fact)
        changes.added = [f for k, f in new_facts.items() if k not in old_facts]
        changes.added.sort(key=lambda f: f.key)
        changes.removed.sort(key=lambda f: f.key)
        changes.changed.sort(key=lambda pair: pair[0].key)
    facts = sorted([*stored.values(), *noise], key=lambda f: f.key)
    return changes, ScanResult(new.scanned_at, new.cluster, facts, dict(new.failed), new.kubectl_ok)


async def rescan(fetch: Fetch, config: dict, memory: Memory, clock=None) -> ScanChanges:
    """Scan again, compare with the stored scan and store the merged result.

    A scan that could not read the cluster changes nothing in memory.
    """
    result = await scan(fetch, config, cluster=config["cluster_name"], memory=memory, clock=clock)
    if not result.ok:
        reason = next(iter(result.failed.values()), "the cluster could not be read")
        return ScanChanges(error=reason)
    changes, stored = compare(load_scan(memory), result)
    save_scan(memory, stored)
    return changes
