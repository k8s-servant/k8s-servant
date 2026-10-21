"""Tests for stored scans and the comparison between two scans."""

import json
from datetime import UTC, datetime

from fakes import fixture_fetch

from k8s_servant.config import configure_app
from k8s_servant.memory import Memory
from k8s_servant.scan.drift import LAST_SCAN_KEY, compare, load_scan, rescan, save_scan
from k8s_servant.scan.facts import Fact, ScanResult
from k8s_servant.scan.inputs import INPUTS

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def _f(key, statement="x", topic="components", inputs=(), scope="platform", setting=None):
    return Fact(key, topic, statement, ("e",), "seen", scope, tuple(inputs), setting=setting)


def _r(facts, failed=None):
    return ScanResult(NOW, "my-cluster", facts, failed or {}, True)


def test_first_scan_is_a_baseline():
    changes, stored = compare(None, _r([_f("component/kyverno"), _f("nodepool/user")]))
    assert changes.baseline and changes.headline() == "First scan recorded: 1 components, 1 node pools"
    assert len(stored.facts) == 2


def test_added_removed_changed():
    old = _r([_f("component/kyverno"), _f("nodepool/user", "3 nodes")])
    new = _r([_f("component/flux"), _f("nodepool/user", "6 nodes")])
    changes, _ = compare(old, new)
    assert [f.key for f in changes.added] == ["component/flux"]
    assert [f.key for f in changes.removed] == ["component/kyverno"]
    assert changes.lines()[-1] == "Changed: 3 nodes → 6 nodes"
    assert changes.headline() == "1 added, 1 removed, 1 changed"


def test_lines_name_the_knowledge_target_and_the_setting():
    new = _r(
        [
            _f("component/flux", "Flux runs", setting=("FLUX_ON", "true")),
            _f("nodepool/user", "3 nodes", scope="cluster"),
        ]
    )
    changes, _ = compare(_r([_f("component/old", "Old runs")]), new)
    assert changes.lines() == [
        "Added: Flux runs — add to knowledge.environment: `- Flux runs` and appConfig.FLUX_ON: true",
        "Added: 3 nodes — add to knowledge.clusterNotes: `- 3 nodes`",
        "Gone: Old runs — remove it from knowledge.environment",
    ]


def test_facts_of_a_failed_input_are_kept_and_never_removed():
    old = _r([_f("network/calico-policies", inputs=("calico-policies",))])
    changes, stored = compare(old, _r([], failed={"calico-policies": "RuntimeError: Forbidden"}))
    assert not changes.removed and [f.key for f in stored.facts] == ["network/calico-policies"]
    assert changes.lines() == ["Not checked this time: calico-policies"]


def test_a_failed_input_drops_the_new_version_and_keeps_the_old():
    old = _r([_f("network/x", "old", inputs=("a",))])
    new = _r([_f("network/x", "new", inputs=("a",))], failed={"a": "boom"})
    changes, stored = compare(old, new)
    assert not changes.changed and stored.facts[0].statement == "old"


def test_a_crashed_detector_hides_removals_but_keeps_additions():
    old = _r([_f("component/kyverno"), _f("component/gone")])
    new = _r([_f("component/flux")], failed={"detector:catalog": "KeyError"})
    changes, stored = compare(old, new)
    assert not changes.removed and [f.key for f in changes.added] == ["component/flux"]
    assert {f.key for f in stored.facts} == {"component/kyverno", "component/gone", "component/flux"}


def test_noise_is_not_compared_but_the_new_noise_is_stored():
    changes, stored = compare(_r([_f("noise/alert/A", topic="noise")]), _r([_f("noise/alert/B", topic="noise")]))
    assert changes.headline() == "no changes"
    assert [f.key for f in stored.facts] == ["noise/alert/B"]


def test_save_and_load_round_trip(tmp_path):
    memory = Memory(tmp_path)
    result = _r([_f("component/flux", setting=("A", "b"))], failed={"x": "y"})
    save_scan(memory, result)
    assert load_scan(memory) == result
    assert load_scan(Memory(tmp_path / "other")) is None


def test_corrupt_or_old_stored_scans_are_ignored(tmp_path):
    memory = Memory(tmp_path)
    good = _f("component/a").to_dict()
    bad_topic = {**good, "topic": "nonsense"}
    bad_certainty = {**good, "certainty": "maybe"}
    bad_scope = {**good, "scope": "galaxy"}
    values = ["not json", json.dumps({"version": 0}), json.dumps({"version": 1, "facts": [{"key": 1}]})]
    for facts in ([bad_topic], [bad_certainty], [bad_scope]):
        values.append(
            json.dumps({"version": 1, "scanned_at": NOW.isoformat(), "cluster": "c", "facts": facts, "failed": {}})
        )
    for value in values:
        memory.set_meta(LAST_SCAN_KEY, value)
        assert load_scan(memory) is None


async def test_a_failed_rescan_leaves_memory_unchanged(tmp_path):
    memory = Memory(tmp_path)
    memory.set_meta(LAST_SCAN_KEY, "keep me")
    fail = {name: RuntimeError("Unable to connect") for name, _, _ in INPUTS}
    changes = await rescan(fixture_fetch("kind", fail=fail), configure_app({}), memory, clock=lambda: NOW)
    assert changes.headline().startswith("failed: ") and memory.get_meta(LAST_SCAN_KEY) == "keep me"


async def test_rescan_twice_reports_no_changes(tmp_path):
    memory = Memory(tmp_path)
    assert (await rescan(fixture_fetch("aks"), configure_app({}), memory, clock=lambda: NOW)).baseline
    assert (await rescan(fixture_fetch("aks"), configure_app({}), memory, clock=lambda: NOW)).headline() == "no changes"


def test_a_failure_present_in_both_scans_is_not_listed_again():
    old = _r([], failed={"calico-policies": "Forbidden"})
    changes, _ = compare(old, _r([], failed={"calico-policies": "Forbidden"}))
    assert changes.not_checked == {} and changes.lines() == []


def test_noise_only_sources_are_never_listed():
    failed = {k: "x" for k in ("prometheus", "alertmanager")}
    changes, _ = compare(_r([]), _r([], failed=failed))
    assert changes.not_checked == {}
    changes, _ = compare(None, _r([], failed=failed))
    assert changes.not_checked == {}


def test_a_new_failure_is_listed_once():
    old = _r([], failed={"a": "x"})
    changes, _ = compare(old, _r([], failed={"a": "x", "b": "y"}))
    assert changes.not_checked == {"b": "y"}


def test_a_baseline_lists_its_failed_inputs():
    changes, _ = compare(None, _r([], failed={"calico-policies": "Forbidden", "prometheus": "x"}))
    assert changes.not_checked == {"calico-policies": "Forbidden"}


def test_stored_scans_with_naive_time_or_non_string_values_are_ignored(tmp_path):
    memory = Memory(tmp_path)
    good = _f("component/a").to_dict()
    base = {"version": 1, "scanned_at": NOW.isoformat(), "cluster": "c", "facts": [good], "failed": {}}
    for change in (
        {"scanned_at": "2026-10-21T09:00:00"},
        {"failed": {"a": 1}},
        {"facts": [{**good, "key": 1}]},
        {"facts": [{**good, "statement": None}]},
    ):
        memory.set_meta(LAST_SCAN_KEY, json.dumps({**base, **change}))
        assert load_scan(memory) is None
    memory.set_meta(LAST_SCAN_KEY, json.dumps(base))
    assert load_scan(memory) is not None


def test_a_broken_configured_url_or_memory_is_listed_when_new():
    failed = {"prometheus-url": "configured URL failed", "memory": "boom", "prometheus": "x"}
    changes, _ = compare(_r([]), _r([], failed=failed))
    assert set(changes.not_checked) == {"prometheus-url", "memory"}
    changes, _ = compare(_r([], failed=failed), _r([], failed=failed))
    assert changes.not_checked == {}


def test_a_crashed_detector_is_listed_on_every_scan():
    failed = {"detector:catalog": "KeyError"}
    changes, _ = compare(_r([], failed=failed), _r([], failed=failed))
    assert changes.not_checked == failed
