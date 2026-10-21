"""Tests for the onboarding questions: confirming unsure facts and filling blind spots."""

import io

from fakes import by_key

from k8s_servant.scan.facts import fact
from k8s_servant.scan.questions import (
    BLIND_SPOTS,
    ask_representative,
    confirm,
    stop_at_eof,
    terminal_ask,
)


def _asker(*answers):
    replies, prompts = list(answers), []

    def ask(prompt):
        prompts.append(prompt)
        return replies.pop(0) if replies else ""

    ask.prompts = prompts
    return ask


def test_answers_confirm_drop_note_and_skip():
    unsure = [fact(f"k/{i}", "network", f"s{i}", ("e",), ("x",), unsure_question="q") for i in range(4)]
    out = {f.key: f for f in confirm(unsure, _asker("y", "n", "a", "Only DNS is allowed.", "s"))}
    assert out["k/0"].certainty == "confirmed" and "k/1" not in out
    assert (out["k/2"].statement, out["k/2"].certainty) == ("Only DNS is allowed.", "told")
    assert out["k/3"].certainty == "unsure"


def test_a_blind_spot_answer_becomes_a_told_fact():
    out = confirm([BLIND_SPOTS[0]], _asker("All egress goes through a\nfirewall."))
    assert (out[0].statement, out[0].certainty) == (
        "Outbound path and allowed destinations: All egress goes through a firewall.",
        "told",
    )


def test_eof_skips_everything():
    facts = [fact("k/0", "network", "s", ("e",), ("x",), unsure_question="q"), *BLIND_SPOTS]
    assert confirm(facts, _asker()) == facts
    assert ask_representative(_asker()) is False


def test_terminal_ask_prompts_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
    assert terminal_ask("Q? ") == ""
    out, err = capsys.readouterr()
    assert (out, err) == ("", "Q? ")


def test_terminal_ask_returns_the_line_without_newline(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
    assert terminal_ask("Q? ") == "yes"


def test_seen_facts_are_never_asked_about():
    ask = _asker()
    confirm([fact("k", "network", "s", ("e",), ("x",))], ask)
    assert ask.prompts == []


def test_an_empty_note_leaves_the_fact_unchanged():
    unsure = fact("k", "network", "s", ("e",), ("x",), unsure_question="q")
    assert confirm([unsure], _asker("a", "  ")) == [unsure]


def test_prompts_use_the_exact_wording():
    unsure = fact("k", "network", "S", ("e",), ("x",), unsure_question="Q")
    ask = _asker()
    confirm([unsure, BLIND_SPOTS[3]], ask)
    assert ask.prompts == [
        "\nS\nQ\n[y]es / [n]o / [a]dd a note / [s]kip: ",
        f"\n{BLIND_SPOTS[3].question}\n(Enter to skip): ",
    ]


def test_representative_accepts_y_and_yes_in_any_case():
    assert ask_representative(_asker("Y")) and ask_representative(_asker("yes"))
    assert not ask_representative(_asker("no"))


def test_blind_spots_are_unanswered_questions():
    assert len({f.key for f in BLIND_SPOTS}) == 6
    assert all(f.key.startswith("blindspot/") and f.statement == "" and f.question for f in BLIND_SPOTS)
    assert all(f.certainty == "unsure" and f.inputs == () and f.evidence == () for f in BLIND_SPOTS)


def test_a_blind_spot_answer_keeps_its_label():
    labels = {
        "blindspot/egress": "Outbound path and allowed destinations",
        "blindspot/private-endpoints": "Private endpoints and peered networks",
        "blindspot/inbound": "In front of inbound traffic",
        "blindspot/maintenance": "Maintenance and node reboot windows",
        "blindspot/schedule": "Stop or recreate schedule",
        "blindspot/external": "Platform components outside the cluster",
    }
    assert {f.key for f in BLIND_SPOTS} == set(labels)
    for spot in BLIND_SPOTS:
        assert confirm([spot], _asker("no"))[0].statement == f"{labels[spot.key]}: no"


def test_the_schedule_question_does_not_invite_a_bare_yes():
    spot = by_key(BLIND_SPOTS, "blindspot/schedule")
    assert spot.question == "When is this cluster stopped or recreated on a schedule, if ever?"


def test_end_of_input_stops_the_questions():
    facts = [fact(f"k/{i}", "network", "s", ("e",), ("x",), unsure_question="q") for i in range(3)]
    ask = _asker("y", None, "y")
    out = confirm(facts, ask)
    assert [f.certainty for f in out] == ["confirmed", "unsure", "unsure"] and len(ask.prompts) == 2
    assert ask_representative(lambda prompt: None) is False


def test_a_note_cut_by_end_of_input_leaves_the_fact():
    unsure = fact("k", "network", "s", ("e",), ("x",), unsure_question="q")
    assert confirm([unsure], _asker("a", None)) == [unsure]


def test_terminal_ask_returns_none_at_end_of_input(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("last"))
    assert terminal_ask("Q? ") == "last"
    assert terminal_ask("Q? ") is None


def test_stop_at_eof_never_prompts_again(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    ask = stop_at_eof(terminal_ask)
    assert ask("one? ") is None and ask("two? ") is None
    assert capsys.readouterr().err == "one? "
