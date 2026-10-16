"""Documentation drift checks.

The README's configuration table is the reference people read. It drifted once
already (2026-10-12), so every setting read in config.py must be listed there, and
every variable in the table must still exist.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG = (ROOT / "k8s_servant" / "config.py").read_text()
README = (ROOT / "README.md").read_text()


def _settings_in_code() -> set[str]:
    return set(re.findall(r'env(?:\.get\()?,? ?"([A-Z][A-Z0-9_]+)"', CONFIG)) | {"LOG_LEVEL"}


def _settings_in_readme() -> set[str]:
    return set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", README, flags=re.MULTILINE))


def test_every_setting_is_documented_in_the_readme():
    missing = _settings_in_code() - _settings_in_readme()
    assert not missing, f"add to the README configuration table: {sorted(missing)}"


def test_the_readme_lists_no_removed_settings():
    stale = _settings_in_readme() - _settings_in_code()
    assert not stale, f"remove from the README configuration table: {sorted(stale)}"


def test_the_code_side_of_this_check_finds_the_settings():
    # Guards the regex: if config.py's style changes, the checks above must not pass vacuously.
    assert {"FOUNDRY_ENDPOINT", "EVENTS_EXCLUDE_REASONS", "CHECK_SKIP_UNCHANGED"} <= _settings_in_code()


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to '-'."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def test_links_between_docs_point_to_existing_files_and_headings():
    pages = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    broken = []
    for page in pages:
        for target in re.findall(r"\]\(([^)\s]+)\)", page.read_text()):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            path, _, anchor = target.partition("#")
            file = (page.parent / path).resolve() if path else page
            if not file.exists():
                broken.append(f"{page.name}: {target} (no such file)")
                continue
            if anchor and file.suffix == ".md":
                slugs = {_slug(h) for h in re.findall(r"^#+ (.+)$", file.read_text(), flags=re.MULTILINE)}
                if anchor not in slugs:
                    broken.append(f"{page.name}: {target} (no such heading)")
    assert not broken, "\n".join(broken)


# --- code documentation --------------------------------------------------------------

import ast  # noqa: E402

PACKAGE = ROOT / "k8s_servant"
LONG_PRIVATE_FUNCTION = 15  # lines; longer private helpers need a docstring too


def _undocumented() -> list[str]:
    """Module docstrings, public defs/classes/methods, and long private functions."""
    missing = []
    for path in sorted(PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text())
        name = path.relative_to(ROOT)
        if not ast.get_docstring(tree):
            missing.append(f"{name}: module")

        def visit(node, inside_function: bool, name: str = name) -> None:
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                    public = not child.name.startswith("_")
                    length = (child.end_lineno or child.lineno) - child.lineno + 1
                    # Closures inside functions (e.g. a tool's run()) are documented by
                    # their builder; long ones still need a docstring.
                    required = (public and not inside_function) or length > LONG_PRIVATE_FUNCTION
                    if required and not ast.get_docstring(child):
                        missing.append(f"{name}:{child.lineno}: {child.name}")
                    visit(child, inside_function or not isinstance(child, ast.ClassDef))

        visit(tree, False)
    return missing


def test_code_is_documented():
    missing = _undocumented()
    assert not missing, "add docstrings:\n" + "\n".join(missing)


def test_readme_repository_structure_lists_every_module():
    tree = README.split("## Repository Structure", 1)[1].split("```", 2)[1]
    modules = {p.name for p in PACKAGE.rglob("*.py") if p.name not in ("__init__.py", "__main__.py")}
    missing = sorted(name for name in modules if name not in tree)
    assert not missing, f"add to the README repository structure: {missing}"
