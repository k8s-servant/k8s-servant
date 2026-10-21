"""Tests for the onboarding questions: confirming unsure facts and filling blind spots."""

import io

from k8s_servant.scan.facts import fact
from k8s_servant.scan.questions import (
    BLIND_SPOTS,
    ask_representative,
    confirm,
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
    assert (out[0].statement, out[0].certainty) == ("All egress goes through a firewall.", "told")


def test_eof_skips_everything():
    facts = [fact("k/0", "network", "s", ("e",), ("x",), unsure_question="q"), *BLIND_SPOTS]
    assert confirm(facts, _asker()) == facts
    assert ask_representative(_asker()) is False


def test_terminal_ask_prompts_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
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
