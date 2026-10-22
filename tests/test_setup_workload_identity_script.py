"""Tests for scripts/setup-workload-identity.sh, run against a fake `az` (no Azure calls).

The fake az records every call and simulates three states through environment
variables: whether things already exist (AZ_EXISTS), whether the Key Vault uses Azure
RBAC or access policies (AZ_KV_RBAC), and which OIDC issuer an existing federated
credential points to (AZ_STORED_ISSUER: a recreated cluster gets a new issuer, so a
re-run must fix the credential).
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup-workload-identity.sh"

ISSUER = "https://westeurope.oic.prod-aks.azure.com/tenant/cluster-now/"
KV_ID = "/subscriptions/kv-sub/resourceGroups/kv-rg/providers/Microsoft.KeyVault/vaults/my-keyvault"
FOUNDRY_ID = (
    "/subscriptions/ai-sub/resourceGroups/my-foundry-rg/providers/Microsoft.CognitiveServices/accounts/my-foundry"
)
# The identity lives in its own long-lived resource group, apart from the cluster's.
ARGS = ("My Subscription", "my-identity-rg", "k8s-servant-identity", "my-cluster-rg", "my-cluster", KV_ID, FOUNDRY_ID)
# One federated credential per cluster, so one identity can serve several clusters.
FED_CRED = "my-cluster-k8s-servant-identity-fed-cred"

FAKE_AZ = f"""#!{sys.executable}
import os, sys
args = sys.argv[1:]
joined = " ".join(args)
with open(os.environ["AZ_LOG"], "a") as log:
    log.write(joined + "\\n")
exists = os.environ.get("AZ_EXISTS") == "1"
if joined.startswith("account show"):
    print("tenant-0000")
elif joined.startswith("group show") and "--query location" in joined:
    print("westeurope")
elif joined.startswith("group show"):
    sys.exit(0 if exists else 3)
elif joined.startswith("identity show") and "clientId" in joined:
    print("client-1111")
elif joined.startswith("identity show") and "principalId" in joined:
    print("principal-2222")
elif joined.startswith("identity show"):
    sys.exit(0 if exists else 3)
elif joined.startswith("aks show"):
    print(os.environ.get("AZ_ISSUER", "{ISSUER}"))
elif joined.startswith("identity federated-credential show"):
    if not exists:
        sys.exit(3)
    print(os.environ.get("AZ_STORED_ISSUER", "{ISSUER}"))
elif joined.startswith("keyvault show"):
    if os.environ.get("AZ_KV_FAIL") == "1":
        sys.exit(3)
    print(os.environ.get("AZ_KV_RBAC", "true"))
elif joined.startswith("role assignment list"):
    print("1" if exists else "0")
"""


@pytest.fixture
def fake_az(tmp_path, monkeypatch):
    """Put a fake `az` first on PATH; return the path of its call log."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    az = bin_dir / "az"
    az.write_text(FAKE_AZ)
    az.chmod(0o755)
    log = tmp_path / "az.log"
    log.write_text("")
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("AZ_LOG", str(log))
    monkeypatch.delenv("MODEL_ROLE", raising=False)
    return log


def _run(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=os.environ.copy())


def test_missing_arguments_are_reported_before_any_azure_call(fake_az):
    result = _run()
    assert result.returncode == 1
    assert "SUBSCRIPTION_NAME (arg 1) is required" in result.stderr
    assert "FOUNDRY_ID (arg 7) is required" in result.stderr
    assert fake_az.read_text() == ""


@pytest.mark.parametrize(
    "position, value, message",
    [
        (5, "my-keyvault", "KEYVAULT_ID (arg 6)"),
        (6, "my-foundry", "FOUNDRY_ID (arg 7)"),
    ],
)
def test_resource_ids_are_validated(fake_az, position, value, message):
    args = list(ARGS)
    args[position] = value
    result = _run(*args)
    assert result.returncode == 1
    assert message in result.stderr
    assert fake_az.read_text() == ""


def test_creates_identity_credential_and_roles_when_nothing_exists(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "0")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    assert "group create --name my-identity-rg --location westeurope" in calls
    assert "identity create --name k8s-servant-identity --resource-group my-identity-rg" in calls
    assert (
        f"identity federated-credential create --name {FED_CRED} "
        "--identity-name k8s-servant-identity --resource-group my-identity-rg "
        f"--issuer {ISSUER} --subject system:serviceaccount:k8s-servant:k8s-servant"
    ) in calls
    assert (
        "role assignment create --assignee-object-id principal-2222 --assignee-principal-type ServicePrincipal "
        f"--role Cognitive Services OpenAI User --scope {FOUNDRY_ID}"
    ) in calls


def test_rbac_key_vault_role_is_scoped_to_the_one_secret(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "0")
    monkeypatch.setenv("AZ_KV_RBAC", "true")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    assert (f"--role Key Vault Secrets User --scope {KV_ID}/secrets/alertmanager-teams-webhook-url") in calls
    assert "keyvault set-policy" not in calls


def test_secret_name_can_be_chosen(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "0")
    result = _run(*ARGS, "other-webhook")
    assert result.returncode == 0, result.stderr
    assert f"--scope {KV_ID}/secrets/other-webhook" in fake_az.read_text()


def test_access_policy_key_vault_is_refused_unless_vault_wide_access_is_allowed(fake_az, monkeypatch):
    # An access policy cannot be limited to one secret: granting it silently would let
    # the agent's identity read every secret in a shared vault.
    monkeypatch.setenv("AZ_EXISTS", "0")
    monkeypatch.setenv("AZ_KV_RBAC", "false")
    result = _run(*ARGS)
    assert result.returncode == 1
    assert "ALLOW_VAULT_WIDE_SECRET_READ" in result.stderr
    assert "keyvault set-policy" not in fake_az.read_text()


def test_access_policy_key_vault_gets_get_permission_when_explicitly_allowed(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "0")
    monkeypatch.setenv("AZ_KV_RBAC", "false")
    monkeypatch.setenv("ALLOW_VAULT_WIDE_SECRET_READ", "yes")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    assert (
        "keyvault set-policy --name my-keyvault --resource-group kv-rg --subscription kv-sub "
        "--object-id principal-2222 --secret-permissions get"
    ) in calls
    assert "Key Vault Secrets User" not in calls
    assert "EVERY secret" in result.stdout + result.stderr


def test_rerun_changes_nothing_when_everything_exists(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "1")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    for change in (
        "group create",
        "identity create",
        "federated-credential create",
        "federated-credential update",
        "role assignment create",
    ):
        assert change not in calls, change


def test_rerun_after_cluster_recreation_points_the_credential_to_the_new_issuer(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "1")
    monkeypatch.setenv("AZ_STORED_ISSUER", "https://westeurope.oic.prod-aks.azure.com/tenant/cluster-old/")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    assert f"identity federated-credential update --name {FED_CRED}" in calls
    assert f"--issuer {ISSUER}" in calls
    assert "federated-credential create" not in calls


def test_missing_oidc_issuer_stops_with_a_hint(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "0")
    monkeypatch.setenv("AZ_ISSUER", "")
    result = _run(*ARGS)
    assert result.returncode == 1
    assert "--enable-oidc-issuer" in result.stderr


def test_prints_the_helm_values(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "1")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    assert 'clientId: "client-1111"' in result.stdout
    assert 'tenantId: "tenant-0000"' in result.stdout
    assert "LLM_AUTH: entra" in result.stdout
    assert "FOUNDRY_" not in result.stdout.replace("FOUNDRY_ID", "")
    assert "k8s-servant release on my-cluster" in result.stdout
    assert "k8s-servant/k8s-servant" in result.stdout


def test_unreadable_key_vault_stops_instead_of_granting_the_whole_vault(fake_az, monkeypatch):
    # Guessing "access policy" when the vault cannot be read would grant every secret.
    monkeypatch.setenv("AZ_EXISTS", "0")
    monkeypatch.setenv("AZ_KV_FAIL", "1")
    result = _run(*ARGS)
    assert result.returncode == 1
    assert "my-keyvault" in result.stderr
    assert "keyvault set-policy" not in fake_az.read_text()


def test_identity_in_the_resource_group_of_an_ephemeral_cluster_is_refused(fake_az, monkeypatch):
    # An ephemeral cluster's resource group is destroyed with it: an identity there
    # would block the destroy or be deleted with it.
    monkeypatch.setenv("EPHEMERAL_CLUSTER", "yes")
    args = list(ARGS)
    args[1] = "my-cluster-rg"
    result = _run(*args)
    assert result.returncode == 1
    assert "EPHEMERAL_CLUSTER" in result.stderr
    assert fake_az.read_text() == ""


def test_identity_in_the_cluster_resource_group_is_allowed_for_a_long_lived_cluster(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "1")
    args = list(ARGS)
    args[1] = "my-cluster-rg"
    result = _run(*args)
    assert result.returncode == 0, result.stderr


def test_prints_the_rerun_reminder_for_an_ephemeral_cluster(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "1")
    monkeypatch.setenv("EPHEMERAL_CLUSTER", "yes")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    assert "Re-run this script after each recreation" in result.stdout
    assert FED_CRED in result.stdout


def test_no_rerun_reminder_for_a_long_lived_cluster(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "1")
    result = _run(*ARGS)
    assert "EPHEMERAL_CLUSTER" not in result.stdout


def test_default_model_role_is_printed(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "0")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    assert "Foundry: 'Cognitive Services OpenAI User'" in result.stdout


@pytest.mark.parametrize("role", ["Cognitive Services User", "Foundry User"])
def test_model_role_can_be_chosen_for_non_openai_models(fake_az, monkeypatch, role):
    # Cognitive Services OpenAI User covers only OpenAI models; Claude and other
    # non-OpenAI models on Foundry need Cognitive Services User (or Foundry User).
    monkeypatch.setenv("AZ_EXISTS", "0")
    monkeypatch.setenv("MODEL_ROLE", role)
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    assert f"--role {role} --scope {FOUNDRY_ID}" in calls
    assert "Cognitive Services OpenAI User" not in calls
    assert f"Foundry: '{role}'" in result.stdout


def test_other_model_roles_are_refused_before_any_azure_call(fake_az, monkeypatch):
    monkeypatch.setenv("MODEL_ROLE", "Owner")
    result = _run(*ARGS)
    assert result.returncode == 1
    assert "MODEL_ROLE" in result.stderr and "Cognitive Services User" in result.stderr
    assert fake_az.read_text() == ""


def test_key_vault_can_be_skipped_with_none(fake_az, monkeypatch):
    # The Teams webhook can come from any Secret; then the identity needs no Key Vault access.
    monkeypatch.setenv("AZ_EXISTS", "0")
    args = list(ARGS)
    args[5] = "none"
    result = _run(*args)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    assert "keyvault" not in calls
    assert "Key Vault Secrets User" not in calls
    assert f"--scope {FOUNDRY_ID}" in calls
    assert "Key Vault:  none" in result.stdout


def test_an_empty_key_vault_argument_is_still_an_error(fake_az):
    args = list(ARGS)
    args[5] = ""
    result = _run(*args)
    assert result.returncode == 1
    assert "KEYVAULT_ID (arg 6) is required" in result.stderr
    assert "none" in result.stderr
    assert fake_az.read_text() == ""
