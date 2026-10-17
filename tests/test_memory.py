from datetime import UTC, datetime, timedelta

import pytest

from k8s_servant.findings import Finding
from k8s_servant.memory import NOTE_MAX_CHARS, Memory, build_save_note_tool

T0 = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
H = timedelta(hours=1)


def F(fingerprint, severity="warning"):
    return Finding(fingerprint, severity, f"title {fingerprint}", f"detail {fingerprint}")


def fps(findings):
    return [f.fingerprint for f in findings]


def test_first_check_reports_everything_as_new(tmp_path):
    memory = Memory(tmp_path)
    diff = memory.apply_check([F("a/x/deployment/one"), F("b/_cluster/node/n1", "critical")], now=T0)
    assert fps(diff.new) == ["b/_cluster/node/n1", "a/x/deployment/one"]  # most severe first
    assert diff.ongoing == [] and diff.resolved == []
    assert diff.open_count == 2 and diff.changed
    assert fps(memory.open_findings()) == ["b/_cluster/node/n1", "a/x/deployment/one"]
    assert memory.open_findings()[0].first_seen == T0


def test_next_check_keeps_ongoing_and_resolves_missing(tmp_path):
    memory = Memory(tmp_path)
    memory.apply_check([F("a"), F("b")], now=T0)
    diff = memory.apply_check([F("a", "critical")], now=T0 + H)
    assert diff.new == []
    assert fps(diff.ongoing) == ["a"]
    assert diff.ongoing[0].severity == "critical"  # updated
    assert diff.ongoing[0].first_seen == T0 and diff.ongoing[0].last_seen == T0 + H
    assert fps(diff.resolved) == ["b"]
    assert diff.resolved[0].status == "resolved" and diff.resolved[0].resolved_at == T0 + H
    assert fps(memory.open_findings()) == ["a"]
    assert sorted(fps(memory.all_findings())) == ["a", "b"]


def test_unchanged_check_is_not_a_change(tmp_path):
    memory = Memory(tmp_path)
    memory.apply_check([F("a")], now=T0)
    assert not memory.apply_check([F("a")], now=T0 + H).changed


def test_reappearing_problem_is_new_again(tmp_path):
    memory = Memory(tmp_path)
    memory.apply_check([F("a")], now=T0)
    memory.apply_check([], now=T0 + H)
    diff = memory.apply_check([F("a")], now=T0 + 2 * H)
    assert fps(diff.new) == ["a"]
    assert diff.new[0].first_seen == T0 + 2 * H and diff.new[0].resolved_at is None


def test_memory_survives_a_restart(tmp_path):
    Memory(tmp_path).apply_check([F("a")], now=T0)
    assert fps(Memory(tmp_path).open_findings()) == ["a"]


def test_runs_are_recorded_and_listed(tmp_path):
    memory = Memory(tmp_path)
    first = memory.record_run("check", T0, 12.5, "ok", "s1", "# r1", 1, 1, 0)
    second = memory.record_run("ask", T0 + H, 3.0, "ok", "s2", "# r2")
    assert [r.id for r in memory.runs()] == [second, first]
    run = memory.run(first)
    assert (run.kind, run.status, run.report_md, run.new_count, run.started_at) == ("check", "ok", "# r1", 1, T0)
    assert memory.run(999) is None
    assert [r.id for r in memory.runs_since(T0 + timedelta(minutes=30))] == [second]


def test_findings_changed_since(tmp_path):
    memory = Memory(tmp_path)
    memory.apply_check([F("old")], now=T0 - 48 * H)
    memory.apply_check([F("new")], now=T0)  # "old" resolved at T0
    new, resolved = memory.findings_changed_since(T0 - 24 * H)
    assert fps(new) == ["new"]
    assert fps(resolved) == ["old"]


def test_meta(tmp_path):
    memory = Memory(tmp_path)
    assert memory.get_meta("last_morning_report_date") is None
    memory.set_meta("last_morning_report_date", "2026-10-12")
    memory.set_meta("last_morning_report_date", "2026-10-13")
    assert memory.get_meta("last_morning_report_date") == "2026-10-13"


def test_notes_are_one_clean_line_each(tmp_path):
    memory = Memory(tmp_path)
    line = memory.add_note("Pod x  restarts\nnightly by design", now=T0)
    assert line == "- 2026-10-12: Pod x restarts nightly by design"
    assert memory.notes() == line + "\n"
    assert (tmp_path / "notes.md").read_text() == line + "\n"


@pytest.mark.parametrize("text", ["", "   ", "x" * (NOTE_MAX_CHARS + 1)])
def test_bad_notes_are_rejected(tmp_path, text):
    with pytest.raises(ValueError):
        Memory(tmp_path).add_note(text)


def test_notes_for_prompt_keeps_the_newest_whole_lines(tmp_path):
    memory = Memory(tmp_path, notes_max_chars=60)
    for i in range(5):
        memory.add_note(f"note number {i} padding", now=T0)
    text = memory.notes_for_prompt()
    assert text.startswith("(older notes omitted)\n- 2026-10-12: ")
    assert text.endswith("note number 4 padding")
    assert "note number 0" not in text


async def test_save_note_tool(tmp_path):
    memory = Memory(tmp_path)
    tool = build_save_note_tool(memory)
    assert (tool.name, tool.surface) == ("save_note", "memory")
    assert (await tool.run({"note": "CronJob backup fails on Sundays by design"})).startswith("Saved: - ")
    assert (await tool.run({})).startswith("ERROR:")
    assert (await tool.run({"note": None})).startswith("ERROR:")
    assert (await tool.run({"note": "y" * 600})).startswith("ERROR:")
    assert memory.notes().count("\n") == 1


def test_notes_since_lists_recent_notes(tmp_path):
    memory = Memory(tmp_path)
    memory.add_note("old fact", now=T0 - 72 * H)
    memory.add_note("new fact", now=T0)
    assert memory.notes_since(T0 - 24 * H) == ["- 2026-10-12: new fact"]


def test_last_run_of_a_kind(tmp_path):
    from datetime import UTC, datetime

    memory = Memory(tmp_path)
    t = datetime(2026, 10, 9, 5, 0, tzinfo=UTC)
    assert memory.last_run("morning") is None
    memory.record_run("morning", t, 1, "ok", "s", "r")
    memory.record_run("morning", t.replace(day=10), 1, "failed", "s", "r")
    memory.record_run("check", t.replace(day=11), 1, "ok", "s", "r")
    assert memory.last_run("morning").started_at == t  # the failed one does not count
