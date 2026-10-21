"""The Markdown body of a scan draft: facts as bullet lines under section headings.

Why: the draft is what a human reviews and what the agent later reads as its
environment description. Each line shows where it came from: a scanned fact ends
with its evidence in brackets, a fact a human gave ends with "(from the team)", and
an unsure fact is never stated, only asked as a `TODO(onboard):` line.
"""

from k8s_servant.scan.facts import SECTION_TITLES, TOPICS, Fact

_MAX_EVIDENCE = 2


def _one_line(text: str) -> str:
    return " ".join(text.split())


def _line(f: Fact) -> str:
    if f.certainty == "unsure":
        return f"- TODO(onboard): {_one_line(f.question)}"
    statement = _one_line(f.statement)
    if f.certainty == "told":
        return f"- {statement} (from the team)"
    evidence = "; ".join([e for e in map(_one_line, f.evidence) if e][:_MAX_EVIDENCE])
    return f"- {statement} [{evidence}]" if evidence else f"- {statement}"


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
        lines = [_line(f) for f in facts if f.scope == scope and f.topic == topic]
        if lines:
            parts.append(f"## {SECTION_TITLES[topic]}\n\n" + "\n".join(lines))
    return "\n\n".join(parts) + "\n" if parts else ""
