"""The Markdown body of a scan draft: facts as bullet lines under section headings.

Why: the draft is what a human reviews and what the agent later reads as its
environment description. Each line shows where it came from: a scanned fact ends
with its evidence in brackets (and is left out without any), a fact a human gave ends with "(from the team)", and
an unsure fact is never stated, only asked as a `TODO(onboard):` line.
"""

from k8s_servant.scan.facts import SECTION_TITLES, TOPICS, Fact

_MAX_EVIDENCE = 2
_MAX_CHARS = 300


def _one_line(text: str) -> str:
    """One line, at most 300 characters: a long cluster value cannot flood the file."""
    text = " ".join(text.split())
    return text if len(text) <= _MAX_CHARS else text[: _MAX_CHARS - 1] + "\u2026"


def _line(f: Fact) -> str | None:
    """The bullet line of a fact; None for a fact that must not be written."""
    if f.certainty == "unsure":
        question = _one_line(f.question)
        return f"- TODO(onboard): {question}" if question else None
    statement = _one_line(f.statement)
    if f.certainty == "told":
        return f"- {statement} (from the team)"
    if f.certainty not in ("seen", "confirmed"):
        return None
    evidence = "; ".join([e for e in map(_one_line, f.evidence) if e][:_MAX_EVIDENCE])
    if evidence:
        return f"- {statement} [{evidence}]"
    # A scanned fact must show its proof; only a human-confirmed one may stand alone.
    return f"- {statement}" if f.certainty == "confirmed" else None


def render_markdown(facts: list[Fact], scope: str, overview: str = "") -> str:
    """The facts of one scope as Markdown, one `##` section per topic.

    Args:
        facts: Facts of any scope; only those of `scope` are rendered.
        scope: `cluster` or `platform`.
        overview: Optional text placed first under `## Overview`.

    Sections follow TOPICS order; a topic without facts gets no section.
    """
    parts = [f"## Overview\n\n{overview.strip()}"] if overview.strip() else []
    for topic in TOPICS:
        lines = [line for f in facts if f.scope == scope and f.topic == topic and (line := _line(f))]
        if lines:
            parts.append(f"## {SECTION_TITLES[topic]}\n\n" + "\n".join(lines))
    return "\n\n".join(parts) + "\n" if parts else ""
