"""Tests for scripts/setup-foundry.sh, run against a fake `az` so no Azure call is made.

The fake az records every invocation and simulates "nothing exists yet" (show
commands fail) or "everything exists" (show commands succeed), so both the create
path and the idempotent re-run path are covered.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup-foundry.sh"

FAKE_AZ = f"""#!{sys.executable}
import os, sys
args = sys.argv[1:]
with open(os.environ["AZ_LOG"], "a") as log:
    log.write(" ".join(args) + "\\n")
exists = os.environ.get("AZ_EXISTS") == "1"
joined = " ".join(args)
if joined.startswith("ad signed-in-user show") and "userPrincipalName" in joined:
    print("me@example.com")
elif joined.startswith("ad signed-in-user show"):
    print("00000000-0000-0000-0000-000000000001")
elif joined.startswith("cognitiveservices account deployment show") and "versionUpgradeOption" in joined:
    print(os.environ.get("AZ_UPGRADE_OPTION", ""))
elif joined.startswith(("group show", "cognitiveservices account deployment show")):
    sys.exit(0 if exists else 3)
elif joined.startswith("cognitiveservices account show") and "--query id" in joined:
    print("/subscriptions/x/resourceGroups/rg/providers/Microsoft.CognitiveServices/accounts/f")
elif joined.startswith("cognitiveservices account show"):
    sys.exit(0 if exists else 3)
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
    return log


def _run(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=os.environ.copy())


ARGS = ("My Subscription", "my-foundry-rg", "my-foundry", "westeurope")


def test_missing_arguments_are_reported(fake_az):
    result = _run()
    assert result.returncode == 1
    assert "SUBSCRIPTION_NAME (arg 1) is required" in result.stderr
    assert "LOCATION (arg 4) is required" in result.stderr
    assert fake_az.read_text() == ""  # nothing touched Azure


@pytest.mark.parametrize(
    "extra, message",
    [
        (("d", "m", "v", "Premium", "50"), "SKU_NAME"),
        (("d", "m", "v", "DataZoneStandard", "lots"), "CAPACITY"),
        (("d", "m", "v", "DataZoneStandard", "50", "Sometimes"), "UPGRADE_OPTION"),
    ],
)
def test_bad_optional_arguments_are_reported(fake_az, extra, message):
    result = _run(*ARGS, *extra)
    assert result.returncode == 1
    assert message in result.stderr


def test_creates_everything_when_nothing_exists(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "0")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    assert "group create --name my-foundry-rg --location westeurope" in calls
    assert (
        "cognitiveservices account create --name my-foundry --resource-group my-foundry-rg "
        "--location westeurope --kind AIServices --sku S0 --custom-domain my-foundry"
    ) in calls
    assert (
        "--deployment-name k8s-servant --model-name gpt-5.4-mini --model-version 2026-03-17 "
        "--model-format OpenAI --sku-name DataZoneStandard --sku-capacity 50"
    ) in calls
    assert "role assignment create" in calls and "Cognitive Services OpenAI User" in calls
    assert "FOUNDRY_ENDPOINT=https://my-foundry.openai.azure.com/" in result.stdout
    assert "FOUNDRY_DEPLOYMENT=k8s-servant" in result.stdout


def test_rerun_changes_nothing_when_everything_exists(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "1")
    monkeypatch.setenv("AZ_UPGRADE_OPTION", "OnceCurrentVersionExpired")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    for create in ("group create", "account create", "deployment create", "role assignment create", "resource update"):
        assert create not in calls, create


UPGRADE_UPDATE = (
    "resource update --ids /subscriptions/x/resourceGroups/rg/providers/Microsoft.CognitiveServices/"
    "accounts/f/deployments/k8s-servant --set properties.versionUpgradeOption={}"
)


def test_new_deployment_keeps_its_version_until_retirement(fake_az, monkeypatch):
    # The portal and CLI default is OnceNewDefaultVersionAvailable, which swaps the
    # model under a tested agent; the script sets the safer policy explicitly.
    monkeypatch.setenv("AZ_EXISTS", "0")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    assert UPGRADE_UPDATE.format("OnceCurrentVersionExpired") in fake_az.read_text()


def test_rerun_fixes_the_upgrade_policy_of_an_existing_deployment(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "1")
    monkeypatch.setenv("AZ_UPGRADE_OPTION", "OnceNewDefaultVersionAvailable")
    result = _run(*ARGS)
    assert result.returncode == 0, result.stderr
    calls = fake_az.read_text()
    assert UPGRADE_UPDATE.format("OnceCurrentVersionExpired") in calls
    assert "deployment create" not in calls


def test_upgrade_policy_can_be_chosen(fake_az, monkeypatch):
    monkeypatch.setenv("AZ_EXISTS", "0")
    result = _run(*ARGS, "k8s-servant", "gpt-5.4-mini", "2026-03-17", "DataZoneStandard", "50", "NoAutoUpgrade")
    assert result.returncode == 0, result.stderr
    assert UPGRADE_UPDATE.format("NoAutoUpgrade") in fake_az.read_text()
    assert "upgrade policy NoAutoUpgrade" in result.stdout
