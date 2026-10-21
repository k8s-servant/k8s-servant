"""Tests for the Markdown body of a scan draft."""

import re
from dataclasses import replace
from datetime import UTC, datetime

from fakes import fixture_fetch

from k8s_servant.config import configure_app
from k8s_servant.scan import scan
from k8s_servant.scan.draft import render_markdown
from k8s_servant.scan.facts import apply_scope, fact

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)


def test_unsure_facts_render_only_as_todo():
    facts = [
        fact(
            "network/x",
            "network",
            "Outbound is denied",
            ("NetworkPolicy shop/deny",),
            ("networkpolicies",),
            unsure_question="Correct?",
        ),
        fact(
            "component/kyverno",
            "components",
            "Kyverno (policy engine) runs in namespace `kyverno`",
            ("CRD group kyverno.io",),
            ("crds",),
        ),
    ]
    text = render_markdown(facts, "platform")
    assert "- TODO(onboard): Correct?" in text and "Outbound is denied" not in text
    assert "## Components\n\n- Kyverno (policy engine) runs in namespace `kyverno` [CRD group kyverno.io]" in text
    assert text.index("## Components") < text.index("## Network")


def test_only_the_scope_asked_for_is_rendered_and_empty_sections_are_left_out():
    facts = [
        fact("platform/kubernetes-version", "platform", "Kubernetes v1.31.0", ("kubectl version",), ("version",)),
        fact("component/flux", "components", "Flux runs", ("CRD group a",), ("crds",)),
    ]
    text = render_markdown(facts, "cluster")
    assert "Kubernetes v1.31.0" in text and "Flux" not in text and "## Components" not in text


def test_evidence_is_capped_at_two_and_text_is_one_line():
    seen = fact("component/a", "components", "A\nruns", ("one", "two", "three"), ("crds",))
    bare = fact("component/b", "components", "B runs", (), ("crds",))
    told = replace(fact("component/c", "components", "C runs", ("x",), ("crds",)), certainty="told")
    text = render_markdown([seen, bare, told], "platform")
    assert "- A runs [one; two]\n" in text
    assert "B runs" not in text
    assert "- C runs (from the team)" in text


def test_overview_comes_first():
    facts = [fact("component/flux", "components", "Flux runs", ("CRD group a",), ("crds",))]
    text = render_markdown(facts, "platform", overview="A small cluster.")
    assert text.startswith("## Overview\n\nA small cluster.\n\n## Components")


async def test_no_fixture_draft_states_anything_without_evidence_or_a_human():
    for flavor in ("aks", "eks", "gke", "kind"):
        result = await scan(fixture_fetch(flavor), configure_app({}), "my-cluster", clock=lambda: NOW)
        facts = apply_scope(result.facts, representative=False)
        for line in render_markdown(facts, "cluster").splitlines():
            if line.startswith("- "):
                assert (
                    line.startswith("- TODO(onboard): ")
                    or line.endswith("(from the team)")
                    or re.search(r" \[[^\]]+\]$", line)
                ), line


def test_unknown_certainty_and_empty_questions_are_left_out():
    odd = replace(fact("component/a", "components", "A runs", ("x",), ("crds",)), certainty="guessed")
    empty = replace(fact("component/b", "components", "B runs", ("x",), ("crds",)), certainty="unsure")
    assert render_markdown([odd, empty], "platform") == ""


def test_long_text_is_capped():
    long = fact("component/a", "components", "S" * 500, ("E" * 500,), ("crds",))
    asked = fact("component/b", "components", "B", ("x",), ("crds",), unsure_question="Q" * 500)
    lines = render_markdown([long, asked], "platform").splitlines()
    assert lines[2] == "- " + "S" * 299 + "\u2026 [" + "E" * 299 + "\u2026]"
    assert lines[3] == "- TODO(onboard): " + "Q" * 299 + "\u2026"


def test_confirmed_without_evidence_is_still_written():
    f = replace(fact("component/a", "components", "A runs", (), ("crds",)), certainty="confirmed")
    assert "- A runs\n" in render_markdown([f], "platform")
