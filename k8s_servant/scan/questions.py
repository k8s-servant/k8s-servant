"""Questions for the person onboarding a cluster: confirm doubts, fill blind spots.

The scan can only see inside the cluster. Facts it is unsure about become
yes/no questions; things it cannot see at all (firewalls, maintenance windows)
are blind spots asked as open questions. Answers are marked `confirmed` or
`told`, so the draft can say they came from the team. Prompts go to stderr so
stdout stays clean for the answer.
"""

import sys
from collections.abc import Callable
from dataclasses import replace

from k8s_servant.scan.facts import Fact, fact

# Shows a prompt and returns the answer line without the newline; "" on EOF.
Ask = Callable[[str], str]

REPRESENTATIVE_QUESTION = (
    "Is this cluster representative of your platform (its components, network design "
    "and monitoring are the same on the other clusters)? [y/n] "
)


def terminal_ask(prompt: str) -> str:
    """Prompt on stderr and read one line from stdin; end of input gives ''."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    return sys.stdin.readline().rstrip("\r\n")


def ask_representative(ask: Ask) -> bool:
    """True when the person says this cluster stands for the whole platform."""
    return ask(REPRESENTATIVE_QUESTION).strip().lower() in ("y", "yes")


def _blind_spot(spot: str, topic: str, scope: str, question: str) -> Fact:
    # An unsure fact without a statement: unanswered, it renders as a TODO line.
    return fact(f"blindspot/{spot}", topic, "", (), (), unsure_question=question, scope=scope)


BLIND_SPOTS: tuple[Fact, ...] = (
    _blind_spot(
        "egress",
        "network",
        "platform",
        "How does outbound traffic leave the cluster (firewall, proxy, NAT gateway), "
        "and which destinations are allowed?",
    ),
    _blind_spot(
        "private-endpoints",
        "network",
        "platform",
        "Which services does the cluster reach through private endpoints or peered networks?",
    ),
    _blind_spot(
        "inbound",
        "network",
        "platform",
        "What sits in front of the cluster for inbound traffic (WAF, application gateway, CDN)?",
    ),
    _blind_spot("maintenance", "noise", "platform", "When are the maintenance and node reboot windows?"),
    _blind_spot("schedule", "noise", "cluster", "Is this cluster stopped or recreated on a schedule? When?"),
    _blind_spot(
        "external",
        "components",
        "platform",
        "Which components of the platform run outside the cluster (mesh servers, DNS, registries, databases)?",
    ),
)


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _told(f: Fact, note: str) -> Fact:
    return replace(f, statement=note, certainty="told")


def confirm(facts: list[Fact], ask: Ask) -> list[Fact]:
    """Ask about every unsure fact and blind spot; return the facts as answered.

    Skipping (or end of input) leaves a fact unchanged. A "no" drops the fact.
    """
    out: list[Fact] = []
    for f in facts:
        if f.certainty != "unsure":
            out.append(f)
        elif not f.statement:
            note = _one_line(ask(f"\n{f.question}\n(Enter to skip): "))
            out.append(_told(f, note) if note else f)
        else:
            answer = ask(f"\n{f.statement}\n{f.question}\n[y]es / [n]o / [a]dd a note / [s]kip: ")
            answer = answer.strip().lower()
            if answer == "y":
                out.append(replace(f, certainty="confirmed"))
            elif answer == "n":
                continue
            elif answer == "a":
                note = _one_line(ask("Your note: "))
                out.append(_told(f, note) if note else f)
            else:
                out.append(f)
    return out
