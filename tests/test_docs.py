"""Documentation drift checks.

The configuration tables in docs/configuration.md are the reference people read. They
drifted once already (2026-10-12), so every setting read in config.py must be listed
there, and every variable in the tables must still exist.
"""

import re
from pathlib import Path

from k8s_servant.llm.providers import OLD_SETTINGS, PROVIDERS

ROOT = Path(__file__).resolve().parent.parent
CONFIG = (ROOT / "k8s_servant" / "config.py").read_text()
README = (ROOT / "README.md").read_text()
CONFIGURATION = (ROOT / "docs" / "configuration.md").read_text()
DOCS = ROOT / "docs"
# Every page of the site (design documents in docs/superpowers/ are not part of it).
SITE_PAGES = sorted(p for p in DOCS.rglob("*.md") if "superpowers" not in p.parts)


def _settings_in_code() -> set[str]:
    names = set(re.findall(r'env(?:\.get\()?,? ?"([A-Z][A-Z0-9_]+)"', CONFIG)) | {"LOG_LEVEL"}
    # _secret(env, "NAME") also reads NAME_FILE.
    return names | {f"{n}_FILE" for n in re.findall(r'_secret\(env, "([A-Z][A-Z0-9_]+)"\)', CONFIG)}


def _settings_in_docs() -> set[str]:
    return set(re.findall(r"^\| `([A-Z][A-Z0-9_]+)` \|", CONFIGURATION, flags=re.MULTILINE))


def test_every_setting_is_documented():
    missing = _settings_in_code() - _settings_in_docs()
    assert not missing, f"add to the tables in docs/configuration.md: {sorted(missing)}"


def test_the_docs_list_no_removed_settings():
    stale = _settings_in_docs() - _settings_in_code()
    assert not stale, f"remove from the tables in docs/configuration.md: {sorted(stale)}"


def test_the_code_side_of_this_check_finds_the_settings():
    # Guards the regex: if config.py's style changes, the checks above must not pass vacuously.
    assert {"LLM_ENDPOINT", "EVENTS_EXCLUDE_REASONS", "CHECK_SKIP_UNCHANGED"} <= _settings_in_code()


def _slug(heading: str) -> str:
    """GitHub's anchor for a heading: lower case, punctuation dropped, spaces to '-'."""
    text = re.sub(r"[^\w\- ]", "", heading.strip().lower())
    return text.replace(" ", "-")


def test_links_between_docs_point_to_existing_files_and_headings():
    pages = [ROOT / "README.md", *SITE_PAGES]
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


def _links(page: Path) -> list[str]:
    return re.findall(r"\]\(([^)\s]+)\)", page.read_text())


def _leaves_docs(page: Path, target: str) -> bool:
    if target.startswith(("http://", "https://", "mailto:", "#")):
        return False
    return not (page.parent / target.partition("#")[0]).resolve().is_relative_to(DOCS)


def test_docs_pages_link_only_inside_docs():
    # The site is built from docs/ alone; a link that leaves it has no page to land on.
    outside = [f"{p.name}: {t}" for p in SITE_PAGES for t in _links(p) if _leaves_docs(p, t)]
    assert not outside, "use https://github.com/k8s-servant/k8s-servant/blob/main/... instead:\n" + "\n".join(outside)


def test_anchors_are_the_same_on_github_and_on_the_site():
    # GitHub keeps every hyphen; the site's renderer merges runs of them and drops a
    # trailing one. Anchors without either work in both places.
    pages = [ROOT / "README.md", *SITE_PAGES]
    anchors = [f"{p.name}: {t}" for p in pages for t in _links(p) if "#" in t and not t.startswith("http")]
    differ = [a for a in anchors if "--" in a.split("#", 1)[1] or a.endswith("-")]
    assert not differ, "reword the heading so its anchor has no '--' or trailing '-':\n" + "\n".join(differ)


# --- model providers ---------------------------------------------------------------

PROVIDERS_PAGE = DOCS / "model-providers.md"
# One recipe page per LLM_PROVIDER value, plus Claude on Foundry (anthropic on a Foundry endpoint).
RECIPES = [*PROVIDERS, "foundry-claude"]


def test_every_provider_has_a_recipe_page_linked_from_the_overview():
    overview = PROVIDERS_PAGE.read_text()
    for name in RECIPES:
        assert (DOCS / "providers" / f"{name}.md").exists(), f"add docs/providers/{name}.md"
        assert f"(providers/{name}.md)" in overview, f"link providers/{name}.md from model-providers.md"


def test_every_example_values_file_is_listed_in_the_readme():
    missing = [f.name for f in sorted((ROOT / "examples").glob("values-*.yaml")) if f.name not in README]
    assert not missing, f"list in the README examples table: {missing}"


def test_no_doc_mentions_old_foundry_settings():
    # The agent names the new setting when it finds an old one; the docs only use the new names.
    pages = [ROOT / "README.md", *SITE_PAGES]
    offenders = [f"{p.name}: {old}" for p in pages for old in OLD_SETTINGS if re.search(rf"\b{old}\b", p.read_text())]
    assert not offenders, "\n".join(offenders)


def test_model_providers_pages_explain_the_apis_and_privacy():
    page = PROVIDERS_PAGE.read_text()
    for needle in ["Why three APIs", "Chat Completions", "Responses", "Messages", "store=false"]:
        assert needle in page, needle
    # The plain-http warning sits where in-cluster http endpoints are configured.
    for name in ["ollama", "openai-compatible"]:
        assert "unencrypted" in (DOCS / "providers" / f"{name}.md").read_text(), name
    assert (
        "https://generativelanguage.googleapis.com/v1beta/openai/"
        in (DOCS / "providers" / "openai-compatible.md").read_text()
    )


def test_the_providers_page_is_linked_from_the_readme():
    assert "docs/model-providers.md" in README


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


def test_repository_structure_lists_every_module():
    page = (ROOT / "docs" / "how-it-works.md").read_text()
    tree = page.split("## Repository structure", 1)[1].split("```", 2)[1]
    modules = {p.name for p in PACKAGE.rglob("*.py") if p.name not in ("__init__.py", "__main__.py")}
    missing = sorted(name for name in modules if name not in tree)
    assert not missing, f"add to the repository structure in docs/how-it-works.md: {missing}"
