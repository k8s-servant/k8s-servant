"""Tests for the Markdown body of a scan draft."""

import re
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import yaml
from fakes import ScriptedChat, answer, fixture_fetch

from k8s_servant.config import configure_app
from k8s_servant.knowledge import load_knowledge
from k8s_servant.scan import scan
from k8s_servant.scan.draft import (
    Setting,
    file_slug,
    header,
    markdown_files,
    render_markdown,
    suggested_settings,
    target_names,
    values_files,
    write_files,
    write_overview,
)
from k8s_servant.scan.facts import ScanResult, apply_scope, fact

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


def _result(facts, failed=None, cluster="my-cluster"):
    return ScanResult(NOW, cluster, facts, failed or {}, True)


async def test_values_files_load_back_as_knowledge(tmp_path):
    result = await scan(fixture_fetch("aks"), configure_app({}), "my-cluster", clock=lambda: NOW)
    facts = apply_scope(result.facts, representative=True)
    files = values_files(result, facts, suggested_settings(facts, configure_app({}), "my-cluster"), True)
    paths = write_files(tmp_path, files, force=False)
    knowledge = load_knowledge(tmp_path / "empty", [str(p) for p in paths])
    assert knowledge.environment.strip() == render_markdown(facts, "platform").strip()
    assert (
        yaml.safe_load(files["my-cluster.yaml"])["appConfig"]["PROMETHEUS_URL"]
        == "http://kps-prometheus.monitoring.svc:9090"
    )


def test_hostile_text_survives_yaml():
    note = fact(
        "blindspot/egress",
        "network",
        'Proxy: http://proxy.example.com:3128 # all "egress" - äöü',
        (),
        (),
        scope="cluster",
    )
    note = replace(note, certainty="told")
    files = values_files(_result([note]), [note], [], False)
    assert (
        'Proxy: http://proxy.example.com:3128 # all "egress" - äöü (from the team)'
        in yaml.safe_load(files["my-cluster.yaml"])["knowledge"]["clusterNotes"]
    )


@pytest.mark.parametrize("statement", ["# heading", "- item", "key: value", "   ", "a\u2028b\x07c"])
def test_odd_statements_and_overviews_keep_the_yaml_valid(statement):
    note = replace(fact("x/y", "network", statement, (), (), scope="cluster"), certainty="told")
    files = values_files(_result([note]), [note], [], False, overview=statement)
    notes = yaml.safe_load(files["my-cluster.yaml"])["knowledge"]["clusterNotes"]
    assert "(from the team)" in notes


def test_hostile_failure_reasons_stay_in_their_comment():
    result = _result([], {"crds": "boom\nknowledge: {evil: 1}\n-->" + "x" * 500})
    for files in (values_files(result, [], [], True), markdown_files(result, [], [], True)):
        for name, text in files.items():
            assert len(text.splitlines()[2]) <= 300
            if name.endswith(".yaml"):
                assert "evil" not in yaml.safe_load(text)["knowledge"]
            if name.endswith(".md"):
                assert text.count("-->") == 1
    assert header(result)[-1].startswith("Not checked: crds (boom") and "\n" not in header(result)[-1]


def test_not_representative_writes_only_the_cluster_file():
    assert set(values_files(_result([]), [], [], False)) == {"my-cluster.yaml"}


def test_a_cluster_called_knowledge_values_does_not_overwrite_the_platform_file():
    files = values_files(_result([], cluster="knowledge-values"), [], [], True)
    assert len(files) == 2


def test_unsure_noise_alone_writes_no_setting_line():
    noisy = fact(
        "noise/event/BackOff",
        "noise",
        "`BackOff` is 60%",
        ("e",),
        (),
        unsure_question="q",
        setting=("EVENTS_EXCLUDE_REASONS", "BackOff"),
    )
    settings = suggested_settings([noisy], configure_app({}), "prod")
    values = values_files(_result([noisy], cluster="prod"), [noisy], settings, False)["prod.yaml"]
    env = markdown_files(_result([noisy], cluster="prod"), [noisy], settings, False)["settings.env"]
    for text in (values, env):
        assert "TODO(onboard): Also hide BackOff?" in text
        assert "# Warning event reasons hidden by default (EVENTS_EXCLUDE_REASONS)" in text
        assert "EVENTS_EXCLUDE_REASONS:" not in text and "EVENTS_EXCLUDE_REASONS=" not in text
    assert "EVENTS_EXCLUDE_REASONS" not in yaml.safe_load(values)["appConfig"]


def test_unsure_noise_is_a_commented_todo_not_a_setting():
    noisy = fact(
        "noise/alert/KubeCPUOvercommit",
        "noise",
        "s",
        ("e",),
        ("prometheus",),
        unsure_question="q",
        setting=("ALERTS_IGNORE_NAMES", "KubeCPUOvercommit"),
    )
    s = {x.name: x for x in suggested_settings([noisy], configure_app({}), "my-cluster")}["ALERTS_IGNORE_NAMES"]
    assert s.value == "" and "Also ignore KubeCPUOvercommit?" in s.todo[0]


def test_confirmed_noise_is_added_and_told_facts_give_no_setting():
    noisy = fact("noise/alert/A", "noise", "s", ("e",), (), setting=("ALERTS_IGNORE_NAMES", "A"))
    confirmed = replace(noisy, certainty="confirmed")
    told = replace(noisy, certainty="told")
    by_name = lambda facts: {x.name: x for x in suggested_settings(facts, configure_app({}), "c")}  # noqa: E731
    assert by_name([confirmed])["ALERTS_IGNORE_NAMES"].value == "Watchdog,InfoInhibitor,A"
    assert "ALERTS_IGNORE_NAMES" not in by_name([told])


def test_unsure_url_fact_gives_a_todo_and_no_value():
    url = fact(
        "monitoring/prometheus-url", "monitoring", "s", ("e",), (), unsure_question="Which Prometheus?", scope="cluster"
    )
    s = {x.name: x for x in suggested_settings([url], configure_app({}), "c")}["PROMETHEUS_URL"]
    assert s.value == "" and s.todo == ("Which Prometheus?",)
    text = values_files(_result([]), [], [s], False)["my-cluster.yaml"]
    assert "PROMETHEUS_URL" not in yaml.safe_load(text)["appConfig"] and "TODO(onboard): Which Prometheus?" in text


def test_placeholder_cluster_name_gets_a_todo():
    assert suggested_settings([], configure_app({}), "my-cluster")[0].todo == ("Set the real cluster name.",)
    assert suggested_settings([], configure_app({}), "prod")[0].todo == ()


def test_file_slug():
    assert file_slug("Prod/AKS 1") == "prod-aks-1" and file_slug("///") == "my-cluster"


def test_existing_files_are_not_overwritten_without_force(tmp_path):
    (tmp_path / "my-cluster.yaml").write_text("mine")
    with pytest.raises(FileExistsError, match=r"my-cluster\.yaml"):
        write_files(tmp_path, {"knowledge-values.yaml": "a", "my-cluster.yaml": "b"}, force=False)
    assert not (tmp_path / "knowledge-values.yaml").exists()
    write_files(tmp_path, {"my-cluster.yaml": "b"}, force=True)
    assert (tmp_path / "my-cluster.yaml").read_text() == "b"


def test_markdown_format_and_settings_env():
    s = [Setting("PROMETHEUS_URL", "http://p.monitoring.svc:9090", "found")]
    files = markdown_files(_result([]), [], s, False)
    assert set(files) == {"cluster.md", "settings.env"}
    assert "# found\nPROMETHEUS_URL=http://p.monitoring.svc:9090" in files["settings.env"]
    assert files["cluster.md"].startswith("<!--\nGenerated by")


def test_settings_env_quotes_values():
    s = [Setting("ALERTS_IGNORE_NAMES", "a; rm -rf $HOME", "r")]
    assert "ALERTS_IGNORE_NAMES='a; rm -rf $HOME'" in markdown_files(_result([]), [], s, False)["settings.env"]


@pytest.mark.parametrize("bad", ["\x1b[31m", "\x07", "\x00", "\ud800"])
def test_control_characters_never_break_a_file(bad):
    result = _result([], {"crds": f"boom{bad}x"}, cluster=f"prod{bad}1")
    settings = suggested_settings([], configure_app({}), result.cluster)
    told = replace(fact("x/t", "network", f"title \x1b]0;pwned\x07 {bad}", (), ()), certainty="told")
    for representative in (True, False):
        facts = apply_scope([told], representative)
        built = (
            values_files(result, facts, settings, representative, f"over{bad}view"),
            markdown_files(result, facts, settings, representative, f"over{bad}view"),
        )
        for files in built:
            for name, text in files.items():
                assert not [c for c in text if ord(c) < 32 and c not in "\n\t"], name
            assert "\ud800" not in text
            if name.endswith(".yaml"):
                assert isinstance(yaml.safe_load(text), dict)


def test_a_no_break_space_stays_readable():
    note = replace(fact("x/y", "network", "uses 10\u00a0GB", (), (), scope="cluster"), certainty="told")
    text = values_files(_result([note]), [note], [], False, overview="needs 10\u00a0GB")["my-cluster.yaml"]
    assert "needs 10 GB" in yaml.safe_load(text)["knowledge"]["clusterNotes"]
    assert "10 GB" in yaml.safe_load(text)["knowledge"]["clusterNotes"]


def test_every_unsure_noise_name_gets_its_own_todo_line():
    names = ["KubeCPUOvercommit", "KubeMemoryOvercommit", "NodeFilesystemAlmostFull", "TargetDown"]
    noisy = [
        fact(
            f"noise/alert/{n}",
            "noise",
            f"`{n}` fired 96% of the last 7 days",
            ("e",),
            (),
            unsure_question=f"`{n}` fired 96% of the last 7 days. Is it expected here, so I stop reporting it?",
            setting=("ALERTS_IGNORE_NAMES", n),
        )
        for n in names
    ]
    settings = suggested_settings(noisy, configure_app({}), "prod")
    text = values_files(_result(noisy, cluster="prod"), noisy, settings, False)["prod.yaml"]
    for n in names:
        assert f"# TODO(onboard): Also ignore {n}? It fired 96% of the last 7 days." in text
    event = fact(
        "noise/event/BackOff",
        "noise",
        "`BackOff` makes up 60% of all warning events",
        ("e",),
        (),
        unsure_question="`BackOff` makes up 60% of all warning events. Hide it by default?",
        setting=("EVENTS_EXCLUDE_REASONS", "BackOff"),
    )
    todo = {s.name: s for s in suggested_settings([event], configure_app({}), "prod")}["EVENTS_EXCLUDE_REASONS"].todo
    assert todo == ("Also hide BackOff? It makes up 60% of all warning events.",)


def test_non_bmp_characters_stay_utf8_in_values():
    s = [Setting("CLUSTER_NAME", "prod \U0001f680", "r")]
    text = values_files(_result([]), [], s, False)["my-cluster.yaml"]
    assert "\U0001f680" in text and yaml.safe_load(text)["appConfig"]["CLUSTER_NAME"] == "prod \U0001f680"


def test_force_never_writes_through_a_symlink(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("keep")
    out = tmp_path / "out"
    out.mkdir()
    (out / "cluster.md").symlink_to(outside)
    write_files(out, {"cluster.md": "new"}, force=True)
    assert outside.read_text() == "keep" and not (out / "cluster.md").is_symlink()
    assert (out / "cluster.md").read_text() == "new"


async def test_overview_gets_only_confirmed_facts():
    chat = ScriptedChat(answer("  A small AKS platform.\n"))
    facts = [
        fact("a", "platform", "Kubernetes v1.33.2", ("kubectl version",), ("version",)),
        fact("b", "network", "Hidden guess", ("e",), ("x",), unsure_question="q"),
        replace(fact("c", "network", "Told by the team", (), ()), certainty="told"),
    ]
    assert await write_overview(chat, facts) == "A small AKS platform."
    sent = chat.calls[0]["turns"][-1].content
    assert "- Kubernetes v1.33.2" in sent and "- Told by the team" in sent and "Hidden guess" not in sent
    assert chat.calls[0]["tools"] == []


@pytest.mark.parametrize("representative", [True, False])
@pytest.mark.parametrize("cluster", ["prod", "knowledge-values", "My Cluster!"])
def test_target_names_match_the_written_files(representative, cluster):
    result = _result([], cluster=cluster)
    s = [Setting("CLUSTER_NAME", cluster, "r")]
    assert target_names("values", cluster, representative) == list(values_files(result, [], s, representative))
    assert target_names("markdown", cluster, representative) == list(markdown_files(result, [], s, representative))


def test_markdown_overview_is_cleaned_like_values():
    overview = "Line one\x00\x1b[31m\u2028line two"
    files = markdown_files(_result([]), [], [], True, overview)
    for text in (files["environment.md"],):
        assert "\x00" not in text and "\x1b" not in text and "\u2028" not in text
        assert "Line one[31m\nline two" in text


async def test_no_proven_facts_means_no_model_call():
    chat = ScriptedChat()  # no replies: a call would fail
    unsure = fact("b", "network", "Hidden guess", ("e",), ("x",), unsure_question="q")
    assert await write_overview(chat, [unsure]) == "" and chat.calls == []


def test_crlf_in_a_value_becomes_one_newline():
    note = replace(fact("x/y", "network", "a", (), (), scope="cluster"), certainty="told")
    text = values_files(_result([note]), [note], [], False, overview="one\r\ntwo")["my-cluster.yaml"]
    assert "one\ntwo" in yaml.safe_load(text)["knowledge"]["clusterNotes"]


def test_noise_already_in_the_defaults_gives_no_setting():
    seen = fact("noise/alert/Watchdog", "noise", "s", ("e",), (), setting=("ALERTS_IGNORE_NAMES", "Watchdog"))
    assert "ALERTS_IGNORE_NAMES" not in {s.name for s in suggested_settings([seen], configure_app({}), "c")}
