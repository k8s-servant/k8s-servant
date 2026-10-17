"""Tests for examples/: the example values files and the Flux HelmRelease.

Users copy these files as a starting point, so they must render the chart, teach the
CLI the same things the cluster learns, and contain only placeholders.
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from k8s_servant.config import DEFAULT_KNOWLEDGE_DIR
from k8s_servant.knowledge import load_knowledge

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
CHART = ROOT / "charts" / "k8s-servant"
VALUES = [EXAMPLES / "knowledge-values.yaml", EXAMPLES / "values-azure.yaml"]
needs_helm = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")


def render() -> list[dict]:
    """Render the chart with the example values files, in the documented order."""
    command = ["helm", "template", "k8s-servant", str(CHART), "--namespace", "k8s-servant"]
    for path in VALUES:
        command += ["-f", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def find(docs: list[dict], kind: str, name: str | None = None) -> dict:
    """Return the one document of `kind` (and `name`, if given)."""
    matches = [d for d in docs if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(matches) == 1
    return matches[0]


@needs_helm
def test_example_values_render_the_chart():
    docs = render()
    config = find(docs, "ConfigMap", "k8s-servant-config")["data"]
    assert config["CLUSTER_NAME"] == "my-cluster"
    assert config["FOUNDRY_ENDPOINT"] == "https://my-foundry.openai.azure.com/openai/v1/"
    assert config["TEAMS_POST_MODE"] == "changes"
    vault = find(docs, "SecretStore")["spec"]["provider"]["azurekv"]["vaultUrl"]
    assert vault == "https://my-keyvault.vault.azure.net"
    assert find(docs, "Deployment")["metadata"]["labels"]["environment"] == "production"


def test_cli_with_the_same_files_knows_the_same():
    # The CLI (`k8s-servant --values ...`) and the cluster read the same files.
    knowledge = load_knowledge(DEFAULT_KNOWLEDGE_DIR, [str(p) for p in VALUES])
    assert knowledge.environment.startswith("# Example platform")
    assert "my-cluster" in knowledge.cluster_notes
    assert "cluster-health-check" in knowledge.skills
    # The text is sent with every request: keep it within a sensible budget.
    assert len(knowledge.environment) + len(knowledge.cluster_notes) < 12_000


def test_examples_hold_only_placeholders_and_no_credentials():
    text = "".join(p.read_text() for p in EXAMPLES.glob("*.yaml"))
    assert not re.search(r"(?i)(password|token|api[-_]?key|client[-_]?secret)\s*[:=]", text)
    guids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text))
    assert guids <= {"00000000-0000-0000-0000-000000000000"}


def test_flux_example_pulls_the_signed_oci_chart_with_both_values_files():
    docs = [d for d in yaml.safe_load_all((EXAMPLES / "flux-helmrelease.yaml").read_text()) if d]
    source = find(docs, "OCIRepository")
    assert source["spec"]["url"] == "oci://ghcr.io/k8s-servant/charts/k8s-servant"
    assert source["spec"]["verify"]["provider"] == "cosign"
    release = find(docs, "HelmRelease")["spec"]
    assert release["chartRef"] == {"kind": "OCIRepository", "name": "k8s-servant"}
    assert release["targetNamespace"] == release["releaseName"] == "k8s-servant"
    assert [v["name"] for v in release["valuesFrom"]] == ["k8s-servant-knowledge", "k8s-servant-values"]
