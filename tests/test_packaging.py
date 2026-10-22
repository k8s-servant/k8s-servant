"""Static checks of the packaging settings in pyproject.toml.

The package list is written out by hand, because the built-in skills are mapped in
from the chart folder (k8s_servant/_knowledge). These tests catch a new subpackage
that was never added, or skills that would be left out of the wheel. The real wheel
is built and installed in CI (the package job in .github/workflows/ci.yml).
"""

import re
import tomllib
from pathlib import Path

from k8s_servant.config import CHECKOUT_KNOWLEDGE_DIR

ROOT = Path(__file__).resolve().parent.parent
SETUPTOOLS = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["setuptools"]


def test_every_subpackage_is_listed():
    found = {".".join(init.parent.relative_to(ROOT).parts) for init in (ROOT / "k8s_servant").rglob("__init__.py")}
    assert found <= set(SETUPTOOLS["packages"]), (
        f"add to [tool.setuptools] packages: {found - set(SETUPTOOLS['packages'])}"
    )


def test_the_wheel_carries_the_chart_skills():
    assert ROOT / SETUPTOOLS["package-dir"]["k8s_servant._knowledge"] == CHECKOUT_KNOWLEDGE_DIR
    assert {"k8s_servant._knowledge", "k8s_servant._knowledge.skills"} <= set(SETUPTOOLS["packages"])
    patterns = SETUPTOOLS["package-data"]["k8s_servant._knowledge.skills"]
    skills_dir = CHECKOUT_KNOWLEDGE_DIR / "skills"
    covered = {p for pattern in patterns for p in skills_dir.glob(pattern)}
    assert covered == set(skills_dir.glob("*/SKILL.md")) and covered


def test_the_pypi_page_links_only_absolute_urls():
    # PyPI shows README.pypi.md without the repository around it, so a relative link breaks.
    text = (ROOT / "README.pypi.md").read_text()
    targets = re.findall(r"\]\(([^)]+)\)|<([^>\s]+)>|src=\"([^\"]+)\"", text)
    assert targets
    for target in filter(None, (t for group in targets for t in group)):
        assert target.startswith("https://"), f"README.pypi.md: use an absolute https:// link, not {target}"
