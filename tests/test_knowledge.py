import textwrap

import pytest

from k8s_servant.knowledge import (
    Knowledge,
    build_load_skill_tool,
    load_knowledge,
    load_knowledge_dir,
    load_values_file,
    parse_skill,
    skill_catalog,
)

SKILL = textwrap.dedent(
    """\
    ---
    name: node-troubleshooting
    description: Use when a node is NotReady.
    ---
    # Node troubleshooting

    1. Check conditions.
    """
)


def test_parse_skill():
    skill = parse_skill("node-troubleshooting", SKILL, "test")
    assert skill.description == "Use when a node is NotReady."
    assert skill.body.startswith("# Node troubleshooting")
    assert "---" not in skill.body


def test_parse_skill_tolerates_crlf_bom_and_missing_frontmatter():
    assert parse_skill("x", "﻿" + SKILL.replace("\n", "\r\n"), "t").description == "Use when a node is NotReady."
    plain = parse_skill("x", "# Just markdown", "t")
    assert plain.description == "" and plain.body == "# Just markdown"


def test_parse_skill_with_broken_yaml_keeps_body():
    skill = parse_skill("x", "---\ndescription: [unclosed\n---\nbody", "t")
    assert skill.description == "" and skill.body == "body"


def test_load_dir_reads_both_layouts(tmp_path):
    (tmp_path / "environment.md").write_text("Platform facts\n")
    (tmp_path / "cluster.md").write_text("Cluster notes\n")
    (tmp_path / "skills" / "node-troubleshooting").mkdir(parents=True)
    (tmp_path / "skills" / "node-troubleshooting" / "SKILL.md").write_text(SKILL)
    (tmp_path / "skill__flux-gitops-status.md").write_text("---\ndescription: Flux.\n---\nbody")
    knowledge = load_knowledge_dir(tmp_path)
    assert knowledge.environment == "Platform facts"
    assert knowledge.cluster_notes == "Cluster notes"
    assert set(knowledge.skills) == {"node-troubleshooting", "flux-gitops-status"}


def test_missing_dir_is_empty(tmp_path):
    assert load_knowledge_dir(tmp_path / "nope") == Knowledge()


def test_values_files_override_in_order(tmp_path):
    (tmp_path / "skills" / "a").mkdir(parents=True)
    (tmp_path / "skills" / "a" / "SKILL.md").write_text("---\ndescription: built-in\n---\nA")
    common = tmp_path / "common.yaml"
    common.write_text(
        textwrap.dedent(
            """\
            knowledge:
              environment: |
                Shared platform facts
              extraSkills:
                a: |
                  ---
                  description: overridden
                  ---
                  A2
            """
        )
    )
    env = tmp_path / "dev.yaml"
    env.write_text("knowledge:\n  clusterNotes: Dev notes\nimage:\n  tag: x\n")
    knowledge = load_knowledge(tmp_path, [str(common), str(env)])
    assert knowledge.environment == "Shared platform facts"
    assert knowledge.cluster_notes == "Dev notes"
    assert knowledge.skills["a"].description == "overridden"
    assert knowledge.skills["a"].body == "A2"


def test_values_file_without_knowledge_section(tmp_path):
    values = tmp_path / "v.yaml"
    values.write_text("image:\n  tag: x\n")
    assert load_values_file(values) == Knowledge()


def test_values_file_with_wrong_shape(tmp_path):
    values = tmp_path / "v.yaml"
    values.write_text("knowledge:\n  extraSkills: [a, b]\n")
    with pytest.raises(ValueError, match="extraSkills"):
        load_values_file(values)


async def test_catalog_and_load_skill_tool():
    knowledge = Knowledge(skills={"n": parse_skill("n", SKILL, "t")})
    assert skill_catalog(knowledge) == "- n: Use when a node is NotReady."
    tool = build_load_skill_tool(knowledge)
    assert tool.name == "load_skill" and tool.surface == "knowledge"
    assert (await tool.run({"name": "n"})).startswith("# Node troubleshooting")
    assert await tool.run({"name": "zzz"}) == "ERROR: no skill named 'zzz'. Available skills: n"
    assert skill_catalog(Knowledge()) == "(no skills available)"
