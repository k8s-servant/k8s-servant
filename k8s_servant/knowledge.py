"""What the agent knows beyond what it can see: environment description and skills.

Three kinds of knowledge, all plain Markdown:

  environment.md   Facts about the platform that the cluster API cannot show
                   (e.g. "Consul servers run on VMs outside the cluster"). Shared
                   by all clusters of one platform.
  cluster.md       Notes for this one cluster (e.g. "recreated every Sunday").
  skills           Step-by-step procedures ("how to investigate a Pending pod"),
                   each a SKILL.md file with YAML frontmatter:

                       ---
                       name: workload-troubleshooting
                       description: Use when a pod is Pending, crashing, ...
                       ---
                       # Markdown body ...

Only the skill names and descriptions go into the system prompt (the "catalog").
The model calls the load_skill tool to read a full skill when it needs one, so
the prompt stays small however many skills there are.

Knowledge is read from two places and merged (later wins):

  1. A knowledge folder (KNOWLEDGE_DIR). Two layouts are understood:
       <dir>/environment.md, <dir>/cluster.md, <dir>/skills/<name>/SKILL.md
       <dir>/skill__<name>.md   flat layout used by the Kubernetes ConfigMap
                                mount, because ConfigMap keys cannot contain "/"
  2. Helm values files (`k8s-servant --values file.yaml`), reading the same
     `knowledge:` keys the Helm chart uses:
       knowledge.environment    -> environment.md
       knowledge.clusterNotes   -> cluster.md
       knowledge.extraSkills    -> {name: SKILL.md content}
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from k8s_servant.tools.registry import Tool

logger = logging.getLogger(__name__)

FLAT_SKILL_PREFIX = "skill__"

# YAML frontmatter: a first line '---', the YAML, then a line '---'.
_FRONTMATTER = re.compile(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", re.DOTALL)


@dataclass(frozen=True)
class Skill:
    """One skill: its catalog entry (name, description) and its full Markdown body."""

    name: str
    description: str
    body: str
    source: str  # where it was loaded from, for logs and `k8s-servant tools`


@dataclass
class Knowledge:
    """Everything the agent is taught, ready to go into the system prompt."""

    environment: str = ""
    cluster_notes: str = ""
    skills: dict[str, Skill] = field(default_factory=dict)

    def merge(self, other: "Knowledge") -> "Knowledge":
        """Return a new Knowledge where the non-empty parts of `other` win."""
        return Knowledge(
            environment=other.environment or self.environment,
            cluster_notes=other.cluster_notes or self.cluster_notes,
            skills={**self.skills, **other.skills},
        )


def parse_skill(name: str, text: str, source: str) -> Skill:
    """Parse SKILL.md text (optional YAML frontmatter + Markdown body).

    The skill's identity is `name` (its folder name or values key), which is
    unique by construction. A different `name:` in the frontmatter only causes a
    warning. Files saved on Windows (CRLF, BOM) are accepted.

    Args:
        name: Skill name.
        text: Full file content.
        source: Where the text came from, for messages.
    """
    text = text.lstrip("﻿").replace("\r\n", "\n")
    description, body = "", text
    match = _FRONTMATTER.match(text)
    if match:
        body = text[match.end() :]
        try:
            meta = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            logger.warning("Skill %s: frontmatter is not valid YAML (%s)", source, exc)
            meta = {}
        if isinstance(meta, dict):
            # Collapse multi-line descriptions to one line for the catalog.
            description = " ".join(str(meta.get("description") or "").split())
            declared = meta.get("name")
            if declared and str(declared) != name:
                logger.warning("Skill %s declares name %r; using %r", source, declared, name)
    if not description:
        logger.warning("Skill %s has no description; the model will only see its name", source)
    return Skill(name=name, description=description, body=body.strip(), source=source)


def _read(path: Path) -> str:
    """Read a UTF-8 file, or return '' if it does not exist."""
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def load_knowledge_dir(directory: str | Path) -> Knowledge:
    """Load environment.md, cluster.md and skills from a folder (both layouts)."""
    path = Path(directory)
    if not path.is_dir():
        logger.warning("Knowledge folder %s does not exist; continuing without it", path)
        return Knowledge()
    skills = {}
    for skill_file in sorted(path.glob("skills/*/SKILL.md")):
        name = skill_file.parent.name
        skills[name] = parse_skill(name, _read(skill_file), str(skill_file))
    # Flat ConfigMap layout. A ConfigMap mount also contains hidden '..data'
    # entries; the 'skill__' prefix keeps them out.
    for skill_file in sorted(path.glob(f"{FLAT_SKILL_PREFIX}*.md")):
        name = skill_file.stem.removeprefix(FLAT_SKILL_PREFIX)
        skills[name] = parse_skill(name, _read(skill_file), str(skill_file))
    return Knowledge(
        environment=_read(path / "environment.md").strip(),
        cluster_notes=_read(path / "cluster.md").strip(),
        skills=skills,
    )


def load_values_file(path: str | Path) -> Knowledge:
    """Read the `knowledge:` section of a Helm values file.

    Raises:
        OSError: If the file cannot be read.
        yaml.YAMLError: If the file is not valid YAML.
        ValueError: If the `knowledge` section has the wrong shape.
    """
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    section = (data.get("knowledge") or {}) if isinstance(data, dict) else {}
    if not isinstance(section, dict):
        raise ValueError(f"{path}: 'knowledge' must be a mapping")
    extra = section.get("extraSkills") or {}
    if not isinstance(extra, dict):
        raise ValueError(f"{path}: 'knowledge.extraSkills' must be a mapping of skill name to SKILL.md content")
    skills = {
        str(name): parse_skill(str(name), str(content), f"{path}#knowledge.extraSkills.{name}")
        for name, content in extra.items()
    }
    return Knowledge(
        environment=str(section.get("environment") or "").strip(),
        cluster_notes=str(section.get("clusterNotes") or "").strip(),
        skills=skills,
    )


def load_knowledge(knowledge_dir: str | Path, values_files: list[str] | None = None) -> Knowledge:
    """Load the knowledge folder, then apply each values file in order."""
    knowledge = load_knowledge_dir(knowledge_dir)
    for values_file in values_files or []:
        knowledge = knowledge.merge(load_values_file(values_file))
    logger.info(
        "Knowledge: environment=%s, cluster notes=%s, skills=%d",
        "yes" if knowledge.environment else "no",
        "yes" if knowledge.cluster_notes else "no",
        len(knowledge.skills),
    )
    return knowledge


def skill_catalog(knowledge: Knowledge) -> str:
    """One line per skill (name: description), for the system prompt."""
    if not knowledge.skills:
        return "(no skills available)"
    return "\n".join(
        f"- {s.name}: {s.description or '(no description)'}"
        for s in sorted(knowledge.skills.values(), key=lambda s: s.name)
    )


def build_load_skill_tool(knowledge: Knowledge) -> Tool:
    """Create the `load_skill` tool that returns a skill's full procedure."""

    async def run(arguments: dict) -> str:
        name = str(arguments.get("name", "")).strip()
        skill = knowledge.skills.get(name)
        if skill is None:
            available = ", ".join(sorted(knowledge.skills)) or "none"
            return f"ERROR: no skill named {name!r}. Available skills: {available}"
        return skill.body

    return Tool(
        name="load_skill",
        description=(
            "Load the full step-by-step procedure of a skill listed in the skill catalog "
            "of your instructions. Load the matching skill before you start investigating, "
            "and follow it."
        ),
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "Skill name exactly as in the catalog"}},
            "required": ["name"],
        },
        run=run,
        surface="knowledge",
    )
