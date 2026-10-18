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


@needs_helm
def test_minimal_example_needs_nothing_cloud_specific():
    command = [
        "helm",
        "template",
        "k8s-servant",
        str(CHART),
        "-n",
        "k8s-servant",
        "-f",
        str(EXAMPLES / "values-minimal.yaml"),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    assert not [d for d in docs if d["apiVersion"].split("/")[0] == "external-secrets.io"]
    assert "annotations" not in find(docs, "ServiceAccount", "k8s-servant")["metadata"]
    pod_labels = find(docs, "Deployment")["spec"]["template"]["metadata"]["labels"]
    assert "azure.workload.identity/use" not in pod_labels
    config = find(docs, "ConfigMap", "k8s-servant-config")["data"]
    assert config["LLM_API_KEY_FILE"] == "/var/run/secrets/llm/api-key"
    assert config["TEAMS_WEBHOOK_URL_FILE"] == "/var/run/secrets/teams/webhook-url"


@needs_helm
def test_azure_csi_example_keeps_the_webhook_out_of_kubernetes_secrets():
    files = [EXAMPLES / "knowledge-values.yaml", EXAMPLES / "values-azure-csi.yaml"]
    command = ["helm", "template", "k8s-servant", str(CHART), "-n", "k8s-servant"]
    for path in files:
        command += ["-f", str(path)]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    docs = [d for d in yaml.safe_load_all(result.stdout) if d]
    assert not [d for d in docs if d["kind"] in ("Secret", "ExternalSecret", "SecretStore")]
    provider = find(docs, "SecretProviderClass")
    spec = find(docs, "Deployment")["spec"]["template"]["spec"]
    volume = {v["name"]: v for v in spec["volumes"]}["keyvault"]["csi"]
    assert volume["volumeAttributes"]["secretProviderClass"] == provider["metadata"]["name"]
    agent = {c["name"]: c for c in spec["containers"]}["agent"]
    mount = {m["name"]: m for m in agent["volumeMounts"]}["keyvault"]
    config = find(docs, "ConfigMap", "k8s-servant-config")["data"]
    assert config["TEAMS_WEBHOOK_URL_FILE"] == f"{mount['mountPath']}/teams-webhook-url"
    assert "teams-webhook-url" in provider["spec"]["parameters"]["objects"]


def find(docs: list[dict], kind: str, name: str | None = None) -> dict:
    """Return the one document of `kind` (and `name`, if given)."""
    matches = [d for d in docs if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(matches) == 1
    return matches[0]


def render_example(name: str) -> list[dict]:
    """Render the chart with one provider example (alone, as a user would)."""
    command = ["helm", "template", "k8s-servant", str(CHART), "-n", "k8s-servant"]
    command += ["-f", str(EXAMPLES / f"values-{name}.yaml")]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return [d for d in yaml.safe_load_all(result.stdout) if d]


def pod_spec(docs: list[dict]) -> dict:
    return find(docs, "Deployment")["spec"]["template"]


@needs_helm
@pytest.mark.parametrize("name", ["anthropic", "openai", "ollama", "azure-claude"])
def test_new_example_renders_without_creating_secrets(name):
    docs = render_example(name)
    assert not [d for d in docs if d["kind"] in ("Secret", "ExternalSecret", "SecretStore")]


@needs_helm
def test_azure_claude_uses_entra_and_workload_identity():
    docs = render_example("azure-claude")
    config = find(docs, "ConfigMap", "k8s-servant-config")["data"]
    assert config["LLM_PROVIDER"] == "anthropic"
    assert config["LLM_AUTH"] == "entra"
    assert config["LLM_ENDPOINT"] == "https://my-foundry.services.ai.azure.com"
    assert config["LLM_MODEL"] == "claude-sonnet-5-5"
    assert "LLM_API_KEY_FILE" not in config
    assert pod_spec(docs)["metadata"]["labels"]["azure.workload.identity/use"] == "true"
    account = find(docs, "ServiceAccount", "k8s-servant")["metadata"]
    assert account["annotations"]["azure.workload.identity/client-id"]


@needs_helm
@pytest.mark.parametrize("name", ["anthropic", "openai"])
def test_key_examples_mount_the_key_file(name):
    docs = render_example(name)
    config = find(docs, "ConfigMap", "k8s-servant-config")["data"]
    assert config["LLM_AUTH"] == "api-key"
    assert config["LLM_API_KEY_FILE"] == "/var/run/secrets/llm/api-key"
    volumes = {v["name"]: v for v in pod_spec(docs)["spec"]["volumes"]}
    assert volumes["llm-api-key"]["secret"]["secretName"] == "k8s-servant-llm"
    assert "azure.workload.identity/use" not in pod_spec(docs)["metadata"]["labels"]


@needs_helm
def test_ollama_example_has_no_key_and_no_workload_identity():
    docs = render_example("ollama")
    config = find(docs, "ConfigMap", "k8s-servant-config")["data"]
    assert config["LLM_PROVIDER"] == "ollama"
    assert config["LLM_ENDPOINT"] == "http://ollama.ollama.svc:11434/v1"
    assert config["LLM_MODEL"] == "qwen3"
    assert "LLM_API_KEY_FILE" not in config
    assert "azure.workload.identity/use" not in pod_spec(docs)["metadata"]["labels"]


@needs_helm
def test_example_values_render_the_chart():
    docs = render()
    config = find(docs, "ConfigMap", "k8s-servant-config")["data"]
    assert config["CLUSTER_NAME"] == "my-cluster"
    assert config["LLM_PROVIDER"] == "foundry"
    assert config["LLM_AUTH"] == "entra"
    assert config["LLM_MODEL"] == "my-deployment"
    assert config["LLM_ENDPOINT"] == "https://my-foundry.openai.azure.com/openai/v1/"
    assert config["TEAMS_POST_MODE"] == "changes"
    # The ESO objects come from extraObjects; the chart only mounts the Secret.
    store = find(docs, "SecretStore")["spec"]["provider"]["azurekv"]
    assert store == {
        "authType": "WorkloadIdentity",
        "vaultUrl": "https://my-keyvault.vault.azure.net",
        "serviceAccountRef": {"name": "k8s-servant"},
    }
    secret = find(docs, "ExternalSecret")["spec"]["target"]["name"]
    teams = {v["name"]: v for v in find(docs, "Deployment")["spec"]["template"]["spec"]["volumes"]}["teams-webhook"]
    assert teams["secret"]["secretName"] == secret
    assert find(docs, "Deployment")["metadata"]["labels"]["environment"] == "production"
    # The Azure example turns Workload Identity on explicitly (the chart default is off).
    account = find(docs, "ServiceAccount", "k8s-servant")["metadata"]
    assert account["annotations"]["azure.workload.identity/client-id"] == "00000000-0000-0000-0000-000000000000"


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
    # A credential-like key with a value on the same line (block headings like
    # `llmApiKey:` hold only a Secret's name below them).
    assert not re.search(r"(?i)(password|token|api[-_]?key|client[-_]?secret)[ \t]*[:=][ \t]*[^\s#]", text)
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
