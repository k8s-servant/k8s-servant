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

# Shows a prompt and returns the answer line without the newline; None at end of input.
Ask = Callable[[str], str | None]

REPRESENTATIVE_QUESTION = (
    "Is this cluster representative of your platform (its components, network design "
    "and monitoring are the same on the other clusters)? [y/n] "
)


def terminal_ask(prompt: str) -> str | None:
    """Prompt on stderr and read one line from stdin; end of input gives None."""
    sys.stderr.write(prompt)
    sys.stderr.flush()
    line = sys.stdin.readline()
    return line.rstrip("\r\n") if line else None


def stop_at_eof(ask: Ask) -> Ask:
    """Wrap `ask` so that after the first end of input it returns None without prompting."""
    ended = False

    def wrapped(prompt: str) -> str | None:
        nonlocal ended
        if ended:
            return None
        answer = ask(prompt)
        ended = answer is None
        return answer

    return wrapped


def ask_representative(ask: Ask) -> bool:
    """True when the person says this cluster stands for the whole platform."""
    return (ask(REPRESENTATIVE_QUESTION) or "").strip().lower() in ("y", "yes")


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
    _blind_spot("schedule", "noise", "cluster", "When is this cluster stopped or recreated on a schedule, if ever?"),
    _blind_spot(
        "external",
        "components",
        "platform",
        "Which components of the platform run outside the cluster (mesh servers, DNS, registries, databases)?",
    ),
)


# What each blind spot is about: an answer such as "no" only makes sense after it.
BLIND_SPOT_LABELS = {
    "blindspot/egress": "Outbound path and allowed destinations",
    "blindspot/private-endpoints": "Private endpoints and peered networks",
    "blindspot/inbound": "In front of inbound traffic",
    "blindspot/maintenance": "Maintenance and node reboot windows",
    "blindspot/schedule": "Stop or recreate schedule",
    "blindspot/external": "Platform components outside the cluster",
}


def _one_line(text: str | None) -> str:
    return " ".join((text or "").split())


def _told(f: Fact, note: str) -> Fact:
    label = BLIND_SPOT_LABELS.get(f.key)
    return replace(f, statement=f"{label}: {note}" if label else note, certainty="told")


def confirm(facts: list[Fact], ask: Ask) -> list[Fact]:
    """Ask about every unsure fact and blind spot; return the facts as answered.

    Skipping leaves a fact unchanged and a "no" drops it. At end of input (None)
    nothing more is asked: that fact and every later one stay as they are.
    """
    out: list[Fact] = []
    ended = False
    for f in facts:
        if ended or f.certainty != "unsure":
            out.append(f)
            continue
        if not f.statement:
            answer = ask(f"\n{f.question}\n(Enter to skip): ")
            note = _one_line(answer)
            out.append(_told(f, note) if note else f)
        else:
            answer = ask(f"\n{f.statement}\n{f.question}\n[y]es / [n]o / [a]dd a note / [s]kip: ")
            choice = (answer or "").strip().lower()
            if choice == "y":
                out.append(replace(f, certainty="confirmed"))
            elif choice == "n":
                continue
            elif choice == "a":
                answer = ask("Your note: ")
                note = _one_line(answer)
                out.append(_told(f, note) if note else f)
            else:
                out.append(f)
        ended = answer is None
    return out
