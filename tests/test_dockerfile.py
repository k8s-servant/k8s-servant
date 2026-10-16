"""Static checks of the Dockerfile and .dockerignore (no Docker needed).

The image runs in clusters where Kyverno enforces non-root containers and where the
root filesystem is read-only, so a few lines of the Dockerfile are requirements, not
style. These tests keep them from being lost in a later edit. The real build is
checked by hand and in the pipeline (docs/development.md).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text() if (ROOT / "Dockerfile").exists() else ""
DOCKERIGNORE = (ROOT / ".dockerignore").read_text() if (ROOT / ".dockerignore").exists() else ""


def test_both_stages_use_python_314_alpine():
    images = re.findall(r"^FROM\s+(\S+)", DOCKERFILE, re.MULTILINE)
    assert len(images) == 2
    assert all(image == "python:3.14-alpine" for image in images)


def test_kubectl_is_pinned_and_checksum_verified():
    # An exact patch version, so builds are reproducible; the minor version is a
    # supported Kubernetes release (one minor of skew covers the clusters around it).
    assert re.search(r"KUBECTL_VERSION=v\d+\.\d+\.\d+\b", DOCKERFILE), "KUBECTL_VERSION must be pinned"
    assert "sha256sum -c" in DOCKERFILE


def test_runtime_image_has_no_pip():
    # pip vendors its own libraries and is not needed to run the agent.
    runtime = DOCKERFILE.split("# Runtime stage")[1]
    assert "pip uninstall -y pip" in runtime


def test_runs_as_numeric_non_root_user():
    # A numeric USER lets the kubelet (and Kyverno's runAsNonRoot) verify it is not root.
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE, re.MULTILINE)
    assert users and users[-1] == "1000"


def test_environment_suits_a_read_only_root_filesystem():
    for setting in ("PYTHONDONTWRITEBYTECODE=1", "PYTHONUNBUFFERED=1", "HOME=/tmp"):
        assert setting in DOCKERFILE


def test_entrypoint_is_the_cli_and_default_command_is_serve():
    assert 'ENTRYPOINT ["k8s-servant"]' in DOCKERFILE
    assert 'CMD ["serve"]' in DOCKERFILE


def test_dockerignore_keeps_secrets_and_local_state_out_of_the_build_context():
    entries = {line.strip().rstrip("/") for line in DOCKERIGNORE.splitlines() if line.strip()}
    for entry in (".env", ".data", ".git", ".venv", "tests", "docs"):
        assert entry in entries, f"{entry} must be in .dockerignore"
