"""Tests for the Helm chart in k8s-servant/, by rendering it with `helm template`.

The chart is where the agent's safety promises become real in a cluster: non-root,
read-only root filesystem, MCP bound to localhost with Secrets denied, RBAC that
never reaches Secrets, the Teams URL only from a Secret file. Each of those is a test
here, so a later edit of a template cannot quietly drop one.

The tests run `helm` as a subprocess and parse its output with PyYAML; they are
skipped when helm is not installed.
"""

import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml

from k8s_servant.knowledge import load_knowledge_dir

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "charts" / "k8s-servant"
LOCAL_MCP_CONFIG = ROOT / "deploy" / "local" / "mcp-config.toml"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm is not installed")

# The minimum a render needs: values without a site default are required on purpose.
REQUIRED = {
    "appConfig.CLUSTER_NAME": "aks-test",
    "appConfig.FOUNDRY_ENDPOINT": "https://example.openai.azure.com/openai/v1/",
}

# Azure Workload Identity is opt-in; these arguments turn it on with test IDs.
CLIENT_ID = "00000000-0000-0000-0000-00000000c11e"
AZURE = [
    "--set",
    "azureWorkloadIdentity.enabled=true",
    "--set-string",
    f"azureWorkloadIdentity.clientId={CLIENT_ID}",
    "--set-string",
    "azureWorkloadIdentity.tenantId=00000000-0000-0000-0000-000000007e7",
]


def render(*args: str, required: bool = True) -> list[dict]:
    """Render the chart and return its documents (fails the test on a helm error)."""
    command = ["helm", "template", "k8s-servant", str(CHART), "--namespace", "k8s-servant"]
    if required:
        for key, value in REQUIRED.items():
            command += ["--set-string", f"{key}={value}"]
    result = subprocess.run([*command, *args], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    return [doc for doc in yaml.safe_load_all(result.stdout) if doc]


def render_error(*args: str) -> str:
    """Render the chart expecting failure; return helm's error text."""
    command = ["helm", "template", "k8s-servant", str(CHART), "--namespace", "k8s-servant"]
    for key, value in REQUIRED.items():
        command += ["--set-string", f"{key}={value}"]
    result = subprocess.run([*command, *args], capture_output=True, text=True, check=False)
    assert result.returncode != 0, "rendering should have failed"
    return result.stderr


def find(docs: list[dict], kind: str, name: str | None = None) -> dict:
    """Return the one document of `kind` (and `name`, if given)."""
    matches = [d for d in docs if d["kind"] == kind and (name is None or d["metadata"]["name"] == name)]
    assert len(matches) == 1, f"expected one {kind} {name or ''}, found {len(matches)}"
    return matches[0]


def containers(docs: list[dict]) -> dict[str, dict]:
    """The Deployment's containers by name."""
    spec = find(docs, "Deployment")["spec"]["template"]["spec"]
    return {c["name"]: c for c in spec["containers"]}


def volumes(docs: list[dict]) -> dict[str, dict]:
    """The pod's volumes by name."""
    return {v["name"]: v for v in find(docs, "Deployment")["spec"]["template"]["spec"]["volumes"]}


def app_config(docs: list[dict]) -> dict[str, str]:
    """The env vars the agent gets from its ConfigMap."""
    return find(docs, "ConfigMap", "k8s-servant-config")["data"]


# --- Deployment ---------------------------------------------------------------------


def test_one_replica_recreated_never_two_at_once():
    deployment = find(render(), "Deployment")
    assert deployment["spec"]["replicas"] == 1
    assert deployment["spec"]["strategy"]["type"] == "Recreate"


def test_pod_runs_agent_and_mcp_sidecar():
    assert set(containers(render())) == {"agent", "mcp"}


@pytest.mark.parametrize("name, uid", [("agent", 1000), ("mcp", 65532)])
def test_containers_are_locked_down(name, uid):
    context = containers(render())[name]["securityContext"]
    assert context["runAsNonRoot"] is True
    assert context["runAsUser"] == uid
    assert context["readOnlyRootFilesystem"] is True
    assert context["allowPrivilegeEscalation"] is False
    assert context["privileged"] is False
    assert context["capabilities"] == {"drop": ["ALL"]}


def test_default_render_needs_no_azure_settings():
    # Only the cluster name and model endpoint are required; nothing Azure-specific.
    docs = render()
    pod = find(docs, "Deployment")["spec"]["template"]
    assert "azure.workload.identity/use" not in pod["metadata"].get("labels", {})
    account = find(docs, "ServiceAccount", "k8s-servant")
    assert "azure.workload.identity/client-id" not in (account["metadata"].get("annotations") or {})


def test_pod_uses_workload_identity_and_its_service_account():
    docs = render(*AZURE)
    pod = find(docs, "Deployment")["spec"]["template"]
    assert pod["metadata"]["labels"]["azure.workload.identity/use"] == "true"
    assert pod["spec"]["serviceAccountName"] == "k8s-servant"
    account = find(docs, "ServiceAccount", "k8s-servant")
    assert account["metadata"]["labels"]["azure.workload.identity/use"] == "true"
    annotations = account["metadata"]["annotations"]
    assert annotations["azure.workload.identity/client-id"] == CLIENT_ID
    assert annotations["azure.workload.identity/tenant-id"]


@pytest.mark.parametrize("key", ["clientId", "tenantId"])
def test_workload_identity_ids_are_required(key):
    assert key in render_error(*AZURE, "--set-string", f"azureWorkloadIdentity.{key}=")


def test_agent_mounts_are_the_only_writable_places():
    docs = render()
    agent = containers(docs)["agent"]
    mounts = {m["mountPath"]: m for m in agent["volumeMounts"]}
    assert set(mounts) >= {"/tmp", "/data", "/knowledge"}
    assert mounts["/knowledge"]["readOnly"] is True
    volumes = {v["name"]: v for v in find(docs, "Deployment")["spec"]["template"]["spec"]["volumes"]}
    assert "emptyDir" in volumes[mounts["/tmp"]["name"]]
    assert volumes[mounts["/data"]["name"]]["persistentVolumeClaim"]["claimName"] == "k8s-servant-data"
    assert volumes[mounts["/knowledge"]["name"]]["configMap"]["name"] == "k8s-servant-knowledge"


def test_mcp_reads_its_config_file():
    docs = render()
    mcp = containers(docs)["mcp"]
    assert mcp["args"] == ["--config", "/etc/mcp/config.toml"]
    mounts = {m["mountPath"]: m for m in mcp["volumeMounts"]}
    assert mounts["/etc/mcp"]["readOnly"] is True


def test_liveness_probe_fails_when_the_heartbeat_is_stale():
    probe = containers(render())["agent"]["livenessProbe"]
    command = " ".join(probe["exec"]["command"])
    assert "/tmp/heartbeat" in command and "300" in command


def test_image_tag_defaults_to_chart_app_version():
    chart = yaml.safe_load((CHART / "Chart.yaml").read_text())
    agent = containers(render())["agent"]
    assert agent["image"].endswith(f"/k8s-servant:{chart['appVersion']}")
    tagged = containers(render("--set", "image.tag=1.2.3"))["agent"]
    assert tagged["image"].endswith("/k8s-servant:1.2.3")


def test_pod_restarts_when_a_config_map_changes():
    annotations = find(render(), "Deployment")["spec"]["template"]["metadata"]["annotations"]
    for key in ("checksum/config", "checksum/knowledge", "checksum/mcp"):
        assert key in annotations


def test_data_volume_survives_uninstall():
    claim = find(render(), "PersistentVolumeClaim", "k8s-servant-data")
    assert claim["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"
    assert claim["spec"]["accessModes"] == ["ReadWriteOnce"]


# --- App settings -------------------------------------------------------------------


def test_app_config_sets_in_cluster_paths_and_values():
    config = app_config(render("--set-string", "appConfig.LOG_LEVEL=DEBUG"))
    assert config["KNOWLEDGE_DIR"] == "/knowledge"
    assert config["DATA_DIR"] == "/data"
    assert config["HEARTBEAT_FILE"] == "/tmp/heartbeat"
    assert config["MCP_URL"] == "http://127.0.0.1:8080/mcp"
    assert config["KUBECTL_PATH"] == "/usr/local/bin/kubectl"
    assert config["CLUSTER_NAME"] == "aks-test"
    assert config["LOG_LEVEL"] == "DEBUG"


def test_empty_app_config_values_are_left_out_so_code_defaults_apply():
    config = app_config(render("--set-string", "appConfig.PROMETHEUS_URL="))
    assert "PROMETHEUS_URL" not in config


def test_agent_reads_its_settings_from_the_config_map():
    agent = containers(render())["agent"]
    assert {"configMapRef": {"name": "k8s-servant-config"}} in agent["envFrom"]


@pytest.mark.parametrize("key", ["DATA_DIR", "KNOWLEDGE_DIR", "MCP_URL"])
def test_app_config_cannot_override_in_cluster_paths(key):
    assert key in render_error("--set-string", f"appConfig.{key}=/elsewhere")


def test_webhook_url_can_never_be_a_plain_setting():
    # The URL is a credential: it may only come from the ESO-created Secret file.
    error = render_error("--set-string", "appConfig.TEAMS_WEBHOOK_URL=https://example.invalid/hook")
    assert "TEAMS_WEBHOOK_URL" in error


def test_api_key_can_never_be_a_plain_setting():
    # A credential: it may only come from a Secret (llmApiKey.existingSecret).
    assert "FOUNDRY_API_KEY" in render_error("--set-string", "appConfig.FOUNDRY_API_KEY=k")


def test_no_api_key_by_default():
    docs = render()
    assert "FOUNDRY_API_KEY_FILE" not in app_config(docs)
    assert "/var/run/secrets/llm" not in {m["mountPath"] for m in containers(docs)["agent"]["volumeMounts"]}


def test_api_key_is_a_file_from_an_existing_secret():
    # A credential: mounted as a file (FOUNDRY_API_KEY_FILE), never an environment variable.
    docs = render("--set-string", "llmApiKey.existingSecret=my-llm-key")
    agent = containers(docs)["agent"]
    assert not any("API_KEY" in e["name"] for e in agent.get("env", []))
    assert app_config(docs)["FOUNDRY_API_KEY_FILE"] == "/var/run/secrets/llm/api-key"
    mounts = {m["mountPath"]: m for m in agent["volumeMounts"]}
    assert mounts["/var/run/secrets/llm"]["readOnly"] is True
    volume = volumes(docs)[mounts["/var/run/secrets/llm"]["name"]]["secret"]
    assert volume["secretName"] == "my-llm-key"
    assert volume["items"] == [{"key": "api-key", "path": "api-key"}]
    assert volume["defaultMode"] & 0o040 and not volume["defaultMode"] & 0o007


def test_api_key_secret_key_is_a_value():
    docs = render("--set-string", "llmApiKey.existingSecret=k", "--set-string", "llmApiKey.secretKey=token")
    assert volumes(docs)["llm-api-key"]["secret"]["items"] == [{"key": "token", "path": "api-key"}]


def test_api_key_file_is_set_by_the_chart():
    assert "FOUNDRY_API_KEY_FILE" in render_error("--set-string", "appConfig.FOUNDRY_API_KEY_FILE=/x")


@pytest.mark.parametrize("key", ["appConfig.CLUSTER_NAME", "appConfig.FOUNDRY_ENDPOINT"])
def test_cluster_name_and_foundry_endpoint_are_required(key):
    assert key.split(".")[1] in render_error("--set-string", f"{key}=")


# --- MCP sidecar config -------------------------------------------------------------


def test_mcp_config_is_read_only_core_localhost_and_denies_secrets():
    toml = tomllib.loads(find(render(), "ConfigMap", "k8s-servant-mcp")["data"]["config.toml"])
    assert toml["read_only"] is True
    assert toml["toolsets"] == ["core"]
    assert toml["bind_address"] == "127.0.0.1"
    assert {"group": "", "version": "v1", "kind": "Secret"} in toml["denied_resources"]


def test_mcp_config_equals_the_local_development_config():
    # One set of MCP settings: what is tested locally is what runs in the cluster.
    rendered = tomllib.loads(find(render(), "ConfigMap", "k8s-servant-mcp")["data"]["config.toml"])
    assert rendered == tomllib.loads(LOCAL_MCP_CONFIG.read_text())


# --- Knowledge ----------------------------------------------------------------------


def write_config_map(data: dict[str, str], directory: Path) -> Path:
    """Write ConfigMap data as files, the way a ConfigMap volume mount does."""
    for key, value in data.items():
        (directory / key).write_text(value)
    return directory


def test_knowledge_config_map_carries_every_built_in_skill(tmp_path):
    data = find(render(), "ConfigMap", "k8s-servant-knowledge")["data"]
    built_in = {p.name for p in (CHART / "knowledge" / "skills").iterdir() if (p / "SKILL.md").is_file()}
    assert {f"skill__{name}.md" for name in built_in} <= set(data)
    # The agent's own loader reads the mounted ConfigMap and finds the same skills.
    knowledge = load_knowledge_dir(write_config_map(data, tmp_path))
    assert set(knowledge.skills) == built_in
    assert all(skill.description for skill in knowledge.skills.values())


def test_knowledge_values_become_environment_cluster_notes_and_extra_skills(tmp_path):
    values = tmp_path / "values.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                "knowledge": {
                    "environment": "# Platform\nFacts.",
                    "clusterNotes": "- recreated every Sunday",
                    "extraSkills": {"site-runbook": "---\nname: site-runbook\ndescription: Use when X.\n---\n# Body"},
                }
            }
        )
    )
    data = find(render("-f", str(values)), "ConfigMap", "k8s-servant-knowledge")["data"]
    mounted = tmp_path / "mount"
    mounted.mkdir()
    knowledge = load_knowledge_dir(write_config_map(data, mounted))
    assert knowledge.environment == "# Platform\nFacts."
    assert knowledge.cluster_notes == "- recreated every Sunday"
    assert knowledge.skills["site-runbook"].description == "Use when X."


# --- Conventions --------------------------------------------------------------------


def test_every_value_has_a_comment():
    lines = (CHART / "values.yaml").read_text().splitlines()
    missing = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")) or ":" not in stripped:
            continue
        if line.startswith((" ", "\t")) and lines[index - 1].strip().endswith(("|", "|-")):
            continue
        previous = lines[index - 1].strip() if index else ""
        if not previous.startswith("#"):
            missing.append(f"line {index + 1}: {stripped}")
    assert not missing, "values.yaml keys without a comment above them:\n" + "\n".join(missing)


def test_chart_lints():
    result = subprocess.run(
        ["helm", "lint", str(CHART), *[arg for k, v in REQUIRED.items() for arg in ("--set-string", f"{k}={v}")]],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- RBAC ---------------------------------------------------------------------------

READ_VERBS = {"get", "list", "watch"}


def cluster_role(docs: list[dict]) -> dict:
    """The agent's own ClusterRole."""
    return find(docs, "ClusterRole", "k8s-servant-cluster-reader")


def test_service_account_is_bound_to_view_and_the_cluster_reader():
    docs = render()
    bindings = [d for d in docs if d["kind"] == "ClusterRoleBinding"]
    roles = {b["roleRef"]["name"] for b in bindings}
    assert roles == {"view", "k8s-servant-cluster-reader"}
    for binding in bindings:
        assert binding["subjects"] == [{"kind": "ServiceAccount", "name": "k8s-servant", "namespace": "k8s-servant"}]


def assert_read_only_rules(rules: list[dict]) -> None:
    """Every rule only reads, never names Secrets, never uses wildcards."""
    for rule in rules:
        assert set(rule["verbs"]) <= READ_VERBS, rule
        assert "*" not in rule.get("resources", []), rule
        assert "*" not in rule.get("apiGroups", []), rule
        assert not any(r.split("/")[0] == "secrets" for r in rule.get("resources", [])), rule
        assert "nonResourceURLs" not in rule, rule
        assert "resourceNames" not in rule, rule


def test_cluster_reader_only_reads_and_never_secrets():
    assert_read_only_rules(cluster_role(render())["rules"])


def test_cluster_reader_never_grants_node_proxy():
    # nodes/proxy reaches the kubelet API, which allows much more than reading.
    resources = {r for rule in cluster_role(render())["rules"] for r in rule["resources"]}
    assert "nodes/proxy" not in resources


def granted(rules: list[dict]) -> set[tuple[str, str]]:
    """(apiGroup, resource) pairs the rules allow."""
    return {(group, resource) for rule in rules for group in rule["apiGroups"] for resource in rule["resources"]}


def test_cluster_reader_covers_what_view_does_not():
    pairs = granted(cluster_role(render())["rules"])
    for pair in [
        ("", "nodes"),
        ("", "persistentvolumes"),
        ("", "namespaces"),
        ("storage.k8s.io", "storageclasses"),
        ("apiextensions.k8s.io", "customresourcedefinitions"),
        ("apiregistration.k8s.io", "apiservices"),
        ("scheduling.k8s.io", "priorityclasses"),
        ("metrics.k8s.io", "nodes"),
        ("metrics.k8s.io", "pods"),
        ("admissionregistration.k8s.io", "validatingwebhookconfigurations"),
        ("helm.toolkit.fluxcd.io", "helmreleases"),
        ("kustomize.toolkit.fluxcd.io", "kustomizations"),
        ("source.toolkit.fluxcd.io", "gitrepositories"),
        ("kyverno.io", "clusterpolicies"),
        # Kyverno CEL policies: admission denials come from these.
        ("policies.kyverno.io", "validatingpolicies"),
        ("policies.kyverno.io", "imagevalidatingpolicies"),
        ("policies.kyverno.io", "generatingpolicies"),
        ("policies.kyverno.io", "mutatingpolicies"),
        ("policies.kyverno.io", "policyexceptions"),
        ("wgpolicyk8s.io", "policyreports"),
        ("external-secrets.io", "externalsecrets"),
        ("gateway.networking.k8s.io", "httproutes"),
        ("cert-manager.io", "certificates"),
        ("monitoring.coreos.com", "prometheusrules"),
    ]:
        assert pair in pairs, pair


def test_extra_rules_are_added_when_read_only(tmp_path):
    values = tmp_path / "values.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                "rbac": {
                    "extraRules": [{"apiGroups": ["example.com"], "resources": ["widgets"], "verbs": ["get", "list"]}]
                }
            }
        )
    )
    assert ("example.com", "widgets") in granted(cluster_role(render("-f", str(values)))["rules"])


@pytest.mark.parametrize(
    "rule",
    [
        {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]},
        {"apiGroups": ["example.com"], "resources": ["*"], "verbs": ["get"]},
        {"apiGroups": ["*"], "resources": ["widgets"], "verbs": ["get"]},
        {"apiGroups": ["example.com"], "resources": ["widgets"], "verbs": ["delete"]},
        {"apiGroups": ["example.com"], "resources": ["widgets"], "verbs": ["*"]},
        {"apiGroups": [""], "resources": ["nodes/proxy"], "verbs": ["get"]},
        # "*/proxy" means every proxy subresource, nodes/proxy included.
        {"apiGroups": [""], "resources": ["*/proxy"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["*/status"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["pods/exec"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["pods/attach"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["pods/portforward"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["services/proxy"], "verbs": ["get"]},
        {"apiGroups": [""], "resources": ["serviceaccounts/token"], "verbs": ["get"]},
    ],
)
def test_extra_rules_that_could_write_or_read_secrets_fail_the_render(tmp_path, rule):
    values = tmp_path / "values.yaml"
    values.write_text(yaml.safe_dump({"rbac": {"extraRules": [rule]}}))
    assert "rbac.extraRules" in render_error("-f", str(values))


# --- Teams webhook via External Secrets ---------------------------------------------

TEAMS_ON = ("--set-string", "teamsWebhook.existingSecret=my-teams-hook")


def test_agent_reads_the_webhook_url_from_a_mounted_file():
    docs = render(*TEAMS_ON)
    agent = containers(docs)["agent"]
    mounts = {m["mountPath"]: m for m in agent["volumeMounts"]}
    assert mounts["/var/run/secrets/teams"]["readOnly"] is True
    volume = volumes(docs)[mounts["/var/run/secrets/teams"]["name"]]["secret"]
    assert volume["secretName"] == "my-teams-hook"
    assert volume["items"] == [{"key": "webhook-url", "path": "webhook-url"}]
    config = app_config(docs)
    assert config["TEAMS_WEBHOOK_URL_FILE"] == "/var/run/secrets/teams/webhook-url"
    assert config["TEAMS_POST_MODE"] == "changes"
    # Never as an environment variable.
    assert not any("env" in c and any("WEBHOOK" in e["name"] for e in c["env"]) for c in containers(docs).values())


def test_teams_secret_key_is_a_value():
    docs = render(*TEAMS_ON, "--set-string", "teamsWebhook.secretKey=url")
    assert volumes(docs)["teams-webhook"]["secret"]["items"] == [{"key": "url", "path": "webhook-url"}]


def test_teams_off_means_no_mount_and_no_posting():
    docs = render()
    mounts = {m["mountPath"] for m in containers(docs)["agent"]["volumeMounts"]}
    assert "/var/run/secrets/teams" not in mounts
    config = app_config(docs)
    assert config["TEAMS_POST_MODE"] == "off"
    assert "TEAMS_WEBHOOK_URL_FILE" not in config


def test_chart_never_creates_secrets_or_secret_sync_objects():
    # How Secrets reach the cluster (kubectl, ESO, Sealed Secrets, CSI, ...) is the
    # user's choice; the chart only references them by name.
    docs = render(*TEAMS_ON, "--set-string", "llmApiKey.existingSecret=k")
    assert not [d for d in docs if d["kind"] in ("Secret", "SecretStore", "ExternalSecret")]


def test_webhook_file_is_readable_by_the_agents_group_only():
    # Secret files belong to root with the pod's fsGroup (1000); the agent (UID 1000)
    # reads them through the group, and nobody else may.
    mode = volumes(render(*TEAMS_ON))["teams-webhook"]["secret"]["defaultMode"]
    assert mode & 0o040 and not mode & 0o007


# --- Identity and extension points ----------------------------------------------------


def values_file(tmp_path: Path, data: dict) -> tuple[str, str]:
    """Write `data` as a values file and return the `-f` arguments for it."""
    path = tmp_path / "values.yaml"
    path.write_text(yaml.safe_dump(data))
    return "-f", str(path)


def test_service_account_and_pod_take_generic_annotations_and_labels(tmp_path):
    # The same fields serve AWS IRSA, GCP Workload Identity and anything else.
    docs = render(
        *values_file(
            tmp_path,
            {
                "serviceAccount": {"annotations": {"eks.amazonaws.com/role-arn": "arn:aws:iam::123:role/x"}},
                "podLabels": {"team": "platform"},
                "podAnnotations": {"example.com/note": "hi"},
            },
        )
    )
    account = find(docs, "ServiceAccount", "k8s-servant")["metadata"]
    assert account["annotations"]["eks.amazonaws.com/role-arn"] == "arn:aws:iam::123:role/x"
    pod = find(docs, "Deployment")["spec"]["template"]["metadata"]
    assert pod["labels"]["team"] == "platform"
    assert pod["annotations"]["example.com/note"] == "hi"
    assert "checksum/config" in pod["annotations"]


def test_workload_identity_keeps_generic_service_account_annotations(tmp_path):
    extra = values_file(tmp_path, {"serviceAccount": {"annotations": {"example.com/a": "b"}}})
    annotations = find(render(*AZURE, *extra), "ServiceAccount", "k8s-servant")["metadata"]["annotations"]
    assert annotations["example.com/a"] == "b"
    assert annotations["azure.workload.identity/client-id"] == CLIENT_ID


def test_extra_env_volumes_and_mounts_reach_the_agent(tmp_path):
    docs = render(
        *values_file(
            tmp_path,
            {
                "extraEnv": [
                    {"name": "HTTPS_PROXY", "value": "http://proxy.example.com:3128"},
                    {"name": "SOME_TOKEN", "valueFrom": {"secretKeyRef": {"name": "s", "key": "k"}}},
                ],
                "extraEnvFrom": [{"configMapRef": {"name": "site-settings"}}],
                "extraVolumes": [{"name": "ca", "configMap": {"name": "corporate-ca"}}],
                "extraVolumeMounts": [{"name": "ca", "mountPath": "/etc/ssl/custom", "readOnly": True}],
            },
        )
    )
    agent = containers(docs)["agent"]
    env = {e["name"]: e for e in agent["env"]}
    assert env["HTTPS_PROXY"]["value"] == "http://proxy.example.com:3128"
    assert env["SOME_TOKEN"]["valueFrom"]["secretKeyRef"] == {"name": "s", "key": "k"}
    assert {"configMapRef": {"name": "site-settings"}} in agent["envFrom"]
    assert {"name": "ca", "mountPath": "/etc/ssl/custom", "readOnly": True} in agent["volumeMounts"]
    assert volumes(docs)["ca"] == {"name": "ca", "configMap": {"name": "corporate-ca"}}
    # The MCP sidecar gets none of it.
    assert "env" not in containers(docs)["mcp"]


def test_extra_objects_are_rendered_with_templates(tmp_path):
    docs = render(
        *values_file(
            tmp_path,
            {
                "extraObjects": [
                    {"apiVersion": "v1", "kind": "ConfigMap", "metadata": {"name": "a"}},
                    "apiVersion: v1\nkind: ConfigMap\nmetadata:\n  name: b\n  namespace: {{ .Release.Namespace }}\n",
                ]
            },
        )
    )
    assert find(docs, "ConfigMap", "a")
    assert find(docs, "ConfigMap", "b")["metadata"]["namespace"] == "k8s-servant"


@pytest.mark.parametrize(
    "name",
    [
        "FOUNDRY_API_KEY",
        "TEAMS_WEBHOOK_URL",
        "SLACK_WEBHOOK_URL",
        "GITHUB_TOKEN",
        "DB_PASSWORD",
        "MY_SECRET",
        "api_key",
    ],
)
def test_extra_env_cannot_carry_a_credential_as_plain_text(tmp_path, name):
    error = render_error(*values_file(tmp_path, {"extraEnv": [{"name": name, "value": "x"}]}))
    assert name in error


@pytest.mark.parametrize(
    "name", ["KNOWLEDGE_DIR", "DATA_DIR", "MCP_URL", "FOUNDRY_API_KEY_FILE", "TEAMS_WEBHOOK_URL_FILE"]
)
def test_extra_env_cannot_override_what_the_chart_sets(tmp_path, name):
    error = render_error(*values_file(tmp_path, {"extraEnv": [{"name": name, "value": "/x"}]}))
    assert name in error


def test_chart_settings_win_over_extra_env_from(tmp_path):
    # With envFrom, a later source wins on duplicate keys. The extra sources come first,
    # so they cannot override chart-set paths or credential files.
    docs = render(*values_file(tmp_path, {"extraEnvFrom": [{"configMapRef": {"name": "site-settings"}}]}))
    env_from = containers(docs)["agent"]["envFrom"]
    assert env_from[0] == {"configMapRef": {"name": "site-settings"}}
    assert env_from[-1] == {"configMapRef": {"name": "k8s-servant-config"}}
