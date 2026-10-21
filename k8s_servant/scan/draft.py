"""The scan draft: Markdown body, suggested settings and the files written to disk.

Why: the draft is what a human reviews and what the agent later reads as its
environment description. Each line shows where it came from: a scanned fact ends
with its evidence in brackets (and is left out without any), a fact a human gave ends with "(from the team)", and
an unsure fact is never stated, only asked as a `TODO(onboard):` line. The optional
overview at the top is written by the model from proven facts only.

The draft goes to disk either as Helm values files (`knowledge:` and `appConfig:`)
or as plain Markdown; both formats also write a `settings.env`. Cluster text is untrusted: values are
written as JSON strings (valid YAML) or shell-quoted, Markdown goes into a literal
block, and anything placed in a `#` comment is collapsed to one short line so it
cannot end the comment early.
"""

import json
import re
import shlex
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from k8s_servant.llm.types import Turn
from k8s_servant.scan.facts import SECTION_TITLES, TOPICS, Fact, ScanResult

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


def count_statements(facts: list[Fact]) -> int:
    """How many facts render as a stated line (not a TODO, not left out for missing evidence)."""
    return sum(1 for f in facts if f.certainty != "unsure" and _line(f))


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


OVERVIEW_PROMPT = (
    "You write the first lines of a Kubernetes platform description for the engineers who run it. "
    "In 2 to 4 short sentences of plain language, say what this platform is and what stands out. "
    "Use only the facts given; do not add anything, do not guess, no headings, no lists."
)


async def write_overview(chat, facts: list[Fact]) -> str:
    """A short plain-language overview, written by the model from proven facts only.

    Unsure facts are left out, so the model cannot turn a guess into a statement.

    Args:
        chat: Model client (`complete(turns, tools)`).
        facts: The draft's facts; only `seen`, `confirmed` and `told` ones are sent.

    Returns:
        The model's reply, stripped; "" without a model call when no fact is proven.
        Model errors propagate to the caller.
    """
    lines = "\n".join(f"- {_one_line(f.statement)}" for f in facts if f.certainty in ("seen", "confirmed", "told"))
    if not lines:
        return ""  # nothing proven: the model would only have the prompt to go on
    reply = await chat.complete([Turn("system", OVERVIEW_PROMPT), Turn("user", lines)], [])
    return reply.content.strip()


_PLACEHOLDER_CLUSTER = "my-cluster"
_MAX_COMMENT = 200
_URL_KEYS = {"monitoring/prometheus-url": "PROMETHEUS_URL", "monitoring/alertmanager-url": "ALERTMANAGER_URL"}
_NOISE = (
    ("ALERTS_IGNORE_NAMES", "alerts_ignore_names", "noise/alert/", "Alerts left out of reports", "ignore"),
    (
        "EVENTS_EXCLUDE_REASONS",
        "events_exclude_reasons",
        "noise/event/",
        "Warning event reasons hidden by default",
        "hide",
    ),
)


@dataclass(frozen=True)
class Setting:
    """One suggested environment setting; an empty `value` means "not known, see the TODO"."""

    name: str
    value: str
    reason: str
    todo: tuple[str, ...] = ()  # TODO(onboard) questions, one comment line each
    placeholder: bool = True  # with no value, write a commented-out `NAME: ""` line


def _clean(text: str, block: bool = False) -> str:
    """Remove what YAML forbids and map Unicode spaces to plain ones.

    Removes control characters (except tab and newline), surrogates and U+FFFE/U+FFFF.
    A line or paragraph separator becomes a newline in a literal block, else a space.
    """
    text = text.replace("\r\n", "\n")
    out = []
    for c in text:
        category = unicodedata.category(c)
        if category in ("Zl", "Zp") or c in "\r\x85":
            out.append("\n" if block else " ")
        elif category == "Zs":
            out.append(" ")
        elif (category in ("Cc", "Cs") and c not in "\t\n") or c in "\ufffe\uffff":
            continue
        else:
            out.append(c)
    return "".join(out)


def _comment(text: str) -> str:
    """Text safe for a `#` comment: one line, at most 200 characters."""
    text = " ".join(_clean(text).split())
    return text if len(text) <= _MAX_COMMENT else text[: _MAX_COMMENT - 1] + "\u2026"


def _why(f: Fact) -> str:
    """The statement of a noise fact without its leading `name`, e.g. "fired 96% of ..."."""
    return re.sub(r"^`[^`]*`\s*", "", f.statement).strip()


def _noise_setting(facts: list[Fact], config: dict, name: str, config_key: str, prefix: str, reason: str, verb: str):
    """The setting for one noise list, or None when there is nothing to add or ask.

    A value is the configured list plus every seen or confirmed name; unsure names
    become TODO lines only.
    """
    noise = [f for f in facts if f.key.startswith(prefix) and f.setting and f.setting[0] == name]
    noise = [f for f in noise if f.certainty != "told"]
    if not noise:
        return None
    values = list(config[config_key])
    added = False
    for f in noise:
        if f.certainty in ("seen", "confirmed") and f.setting[1] not in values:
            values.append(f.setting[1])
            added = True
    todo = tuple(f"Also {verb} {f.setting[1]}? It {_why(f)}." for f in noise if f.certainty == "unsure")
    if added:
        return Setting(name, ",".join(values), reason, todo)
    if not todo:
        return None
    # Without a proven name there is nothing to set: writing today's list would pin the defaults.
    # The comment names the setting, since no setting line follows.
    return Setting(name, "", f"{reason} ({name})", todo, placeholder=False)


def suggested_settings(facts: list[Fact], config: dict, cluster: str) -> list[Setting]:
    """Settings worth putting in the draft; only seen or confirmed facts give a value.

    An unsure fact gives its question as a TODO and no value; a `told` fact (the human
    wrote their own note instead) never gives a setting, because the note may disagree.
    """
    todo = ("Set the real cluster name.",) if cluster == _PLACEHOLDER_CLUSTER else ()
    settings = [Setting("CLUSTER_NAME", cluster, "Name shown in reports and Teams cards", todo)]
    for key, name in _URL_KEYS.items():
        for f in facts:
            if f.key != key or f.certainty == "told":
                continue
            reason = f.setting_reason or f"Address of {name.split('_')[0].capitalize()}"
            if f.certainty == "unsure" or not f.setting:
                settings.append(Setting(name, "", reason, (f.question,) if f.question else ()))
            else:
                settings.append(Setting(f.setting[0], f.setting[1], reason))
            break
    for name, config_key, prefix, reason, verb in _NOISE:
        noise = _noise_setting(facts, config, name, config_key, prefix, reason, verb)
        if noise:
            settings.append(noise)
    return settings


def file_slug(cluster: str) -> str:
    """A file-name-safe form of the cluster name; `my-cluster` when nothing is left."""
    return re.sub(r"[^a-z0-9-]+", "-", cluster.lower()).strip("-") or _PLACEHOLDER_CLUSTER


def header(result: ScanResult) -> list[str]:
    """Comment lines (without any comment marker) saying where the draft came from."""
    when = result.scanned_at.strftime("%Y-%m-%d %H:%M")
    lines = [
        f"Generated by `k8s-servant onboard` from cluster {_comment(result.cluster)} on {when} UTC.",
        "Review before use: check every line, answer each TODO(onboard), then commit.",
    ]
    if result.failed:
        lines.append(
            "Not checked: "
            + "; ".join(f"{_comment(name)} ({_comment(reason)})" for name, reason in result.failed.items())
        )
    return lines


def _block(text: str) -> str:
    """Markdown as the body of a YAML `|` block (4 spaces deep) that cannot break out of it."""
    lines = [line.rstrip() for line in _clean(text, block=True).strip().splitlines()]
    return "\n".join(f"    {line}" if line else "" for line in lines)


def _yaml_text(key: str, text: str) -> str:
    body = _block(text)
    return f"  {key}: |\n{body}\n" if body else f'  {key}: ""\n'


def _cluster_file(cluster: str) -> str:
    name = file_slug(cluster)
    # `knowledge-values.yaml` is the platform file; a cluster of that name must not overwrite it.
    return f"{name}-cluster.yaml" if name == "knowledge-values" else f"{name}.yaml"


def target_names(fmt: str, cluster: str, representative: bool = True) -> list[str]:
    """The file names `values_files` (fmt `values`) or `markdown_files` (`markdown`) write.

    Lets a caller check for existing files before scanning; with `representative`
    True it names every file a run could write.
    """
    if fmt == "values":
        return (["knowledge-values.yaml"] if representative else []) + [_cluster_file(cluster), "settings.env"]
    return (["environment.md"] if representative else []) + ["cluster.md", "settings.env"]


def _texts(facts: list[Fact], representative: bool, overview: str) -> tuple[str, str]:
    """The platform and cluster bodies, cleaned for every file format alike."""
    environment = render_markdown(facts, "platform", overview) if representative else ""
    notes = render_markdown(facts, "cluster", "" if representative else overview)
    return _clean(environment, block=True), _clean(notes, block=True)


def _settings_env(result: ScanResult, settings: list[Setting]) -> str:
    """The suggested settings as a shell-sourceable `settings.env`, shared by both formats."""
    env = "".join(f"# {line}\n" for line in header(result))
    for s in settings:
        env += f"\n# {_comment(s.reason)}\n"
        env += "".join(f"# TODO(onboard): {_comment(t)}\n" for t in s.todo)
        if s.value:
            env += f"{s.name}={shlex.quote(_clean(s.value))}\n"
        elif s.placeholder:
            env += f"# {s.name}=\n"
    return env


def values_files(
    result: ScanResult, facts: list[Fact], settings: list[Setting], representative: bool, overview: str = ""
) -> dict[str, str]:
    """Helm values files (platform when representative, this cluster) plus `settings.env`.

    `--values` loads only the knowledge part on a laptop, so the settings are also
    written for `set -a; source settings.env; set +a`.
    """
    environment, notes = _texts(facts, representative, overview)
    top = "".join(f"# {line}\n" for line in header(result))
    platform_name, cluster_name, _ = target_names("values", result.cluster, True)
    files = {}
    if representative:
        files[platform_name] = top + "knowledge:\n" + _yaml_text("environment", environment)
    config = ""
    for s in settings:
        config += f"  # {_comment(s.reason)}\n"
        config += "".join(f"  # TODO(onboard): {_comment(t)}\n" for t in s.todo)
        if s.value:
            config += f"  {s.name}: {json.dumps(_clean(s.value), ensure_ascii=False)}\n"
        elif s.placeholder:
            config += f'  # {s.name}: ""\n'
    # With only commented-out settings, a bare `appConfig:` would be null, not a mapping.
    app_config = ("appConfig:\n" if any(s.value for s in settings) else "appConfig: {}\n") + config
    files[cluster_name] = top + "knowledge:\n" + _yaml_text("clusterNotes", notes) + app_config
    files["settings.env"] = _settings_env(result, settings)
    return files


def _html_comment(lines: list[str]) -> str:
    safe = []
    for line in lines:
        while "--" in line:
            line = line.replace("--", "- -")
        safe.append(line)
    return "<!--\n" + "\n".join(safe) + "\n-->\n\n"


def markdown_files(
    result: ScanResult, facts: list[Fact], settings: list[Setting], representative: bool, overview: str = ""
) -> dict[str, str]:
    """Plain files: `environment.md` (when representative), `cluster.md` and `settings.env`."""
    environment, notes = _texts(facts, representative, overview)
    top = _html_comment(header(result))
    files = {}
    if representative:
        files["environment.md"] = top + environment
    files["cluster.md"] = top + notes
    files["settings.env"] = _settings_env(result, settings)
    return files


class DraftExists(FileExistsError):
    """Draft files already exist; raised by us, unlike the OS's own FileExistsError."""


def refuse_existing(out_dir: Path, names: list[str]) -> None:
    """Raise when any of `names` already exists in `out_dir` (a dangling symlink counts).

    Raises:
        DraftExists: Naming every target that already exists.
    """
    existing = [name for name in names if (Path(out_dir) / name).exists() or (Path(out_dir) / name).is_symlink()]
    if existing:
        raise DraftExists("Already exist (use --force to overwrite): " + ", ".join(existing))


def write_files(out_dir: Path, files: dict[str, str], force: bool) -> list[Path]:
    """Write the files into `out_dir`; without `force` nothing is written if any exists.

    Raises:
        DraftExists: Naming every target that already exists.
        OSError: The folder or a file could not be written.
    """
    out_dir = Path(out_dir)
    targets = [out_dir / name for name in files]
    if not force:
        refuse_existing(out_dir, list(files))
    out_dir.mkdir(parents=True, exist_ok=True)
    for path, text in zip(targets, files.values(), strict=True):
        # Replace, never write through: an existing symlink could point anywhere.
        path.unlink(missing_ok=True)
        path.write_text(text, encoding="utf-8")
    return targets
