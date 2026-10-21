import asyncio
import os
import sys

import pytest
import yaml
from fakes import ScriptedChat, answer, findings_answer, fixture_fetch

from k8s_servant import cli
from k8s_servant.config import configure_app
from k8s_servant.memory import Memory
from k8s_servant.scan import scan
from k8s_servant.scan.drift import load_scan, save_scan

ENV_VARS = (
    "LLM_PROVIDER",
    "LLM_ENDPOINT",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_API_KEY_FILE",
    "LLM_AUTH",
    "AZURE_FEDERATED_TOKEN_FILE",
    "KNOWLEDGE_DIR",
    "MAX_ITERATIONS",
    "PROMETHEUS_URL",
    "ALERTMANAGER_URL",
    "TEAMS_WEBHOOK_URL",
    "TEAMS_WEBHOOK_URL_FILE",
    "TEAMS_POST_MODE",
    "CLUSTER_NAME",
    "HEARTBEAT_FILE",
    "CHECK_SKIP_UNCHANGED",
    "CHECK_FULL_INTERVAL_HOURS",
    "SCAN_BEFORE_REPORT",
    "TOOL_OUTPUT_MAX_CHARS",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Tests must not depend on the developer's shell settings or touch ./.data."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    # Checks run the cluster summary tools (kubectl) before the model; a unit test
    # must never read the developer's real cluster.
    fake_kubectl = tmp_path / "kubectl"
    fake_kubectl.write_text("#!/bin/sh\necho '{\"items\": []}'\n")
    fake_kubectl.chmod(0o755)
    monkeypatch.setenv("KUBECTL_PATH", str(fake_kubectl))


@pytest.fixture
def scripted_model(monkeypatch):
    """Configure a model and replace it with a scripted fake; returns a setter for the replies."""
    monkeypatch.setenv("LLM_PROVIDER", "foundry")
    monkeypatch.setenv("LLM_ENDPOINT", "https://x.openai.azure.com/")
    monkeypatch.setenv("LLM_MODEL", "k8s-servant")
    monkeypatch.setenv("MCP_URL", "")
    monkeypatch.setenv("CLUSTER_NAME", "my-cluster")
    holder = {}
    monkeypatch.setattr(cli, "build_chat", lambda config: holder["chat"])

    def script(*replies):
        holder["chat"] = ScriptedChat(*replies)
        return holder["chat"]

    return script


CRASH = {
    "fingerprint": "crashloop/payments-dev/Deployment/api",
    "severity": "critical",
    "title": "api crash-loops",
    "detail": "exit code 1",
}


def test_parser_requires_a_command():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


def test_parser_collects_values_and_question_words():
    args = cli.build_parser().parse_args(["--values", "a.yaml", "--values", "b.yaml", "ask", "any", "broken", "pods?"])
    assert args.values == ["a.yaml", "b.yaml"]
    assert args.question == ["any", "broken", "pods?"]


def test_ask_without_model_settings_exits_2(capsys):
    assert cli.main(["ask", "hi"]) == 2
    assert "LLM_PROVIDER" in capsys.readouterr().err


@pytest.mark.parametrize("command", [["check"], ["report"], ["serve"]])
def test_model_commands_need_model_settings(command, capsys):
    assert cli.main(command) == 2
    assert "LLM_PROVIDER" in capsys.readouterr().err


def test_missing_values_file_is_a_configuration_error(tmp_path, capsys):
    assert cli.main(["--values", str(tmp_path / "missing.yaml"), "tools"]) == 2
    assert "Configuration error" in capsys.readouterr().err


def test_malformed_values_file_is_a_configuration_error(tmp_path, capsys):
    bad = tmp_path / "bad.yaml"
    bad.write_text("knowledge: [unclosed\n")
    assert cli.main(["--values", str(bad), "tools"]) == 2
    assert "Configuration error" in capsys.readouterr().err


def test_bad_integer_setting_is_a_configuration_error(monkeypatch, capsys):
    monkeypatch.setenv("MAX_ITERATIONS", "many")
    assert cli.main(["tools"]) == 2
    assert "MAX_ITERATIONS" in capsys.readouterr().err


def test_tools_shows_kubectl_fallback_and_skills(monkeypatch, capsys):
    monkeypatch.setenv("MCP_URL", "")
    assert cli.main(["tools"]) == 0
    out = capsys.readouterr().out
    assert "kubectl fallback" in out
    assert "load_skill" in out and "save_note" in out
    assert "cluster-health-check" in out


def test_doctor_reports_missing_model_settings(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MCP_URL", "")
    monkeypatch.setenv("KUBECTL_PATH", str(tmp_path / "no-kubectl"))
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] config" in out
    assert "[SKIP] model" in out
    assert "[OK  ] memory" in out
    assert "[WARN] prometheus" in out and "[WARN] teams" in out


def _doctor_env(monkeypatch, provider, endpoint="", model="claude-sonnet-5-5"):
    monkeypatch.setenv("MCP_URL", "")
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("LLM_MODEL", model)
    if endpoint:
        monkeypatch.setenv("LLM_ENDPOINT", endpoint)
    monkeypatch.setattr(cli, "build_chat", lambda config: ScriptedChat(answer("OK")))


def test_doctor_names_provider_api_and_key_file(monkeypatch, tmp_path, capsys):
    key_file = tmp_path / "key"
    key_file.write_text("sk-test-secret\n")
    _doctor_env(monkeypatch, "anthropic")
    monkeypatch.setenv("LLM_API_KEY_FILE", str(key_file))
    assert cli.main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "anthropic / claude-sonnet-5-5 via Messages API, API key (file " in out
    assert "sk-test-secret" not in out
    assert "[OK  ] model" in out


def test_doctor_fails_config_on_invalid_auth_combo(monkeypatch, capsys):
    _doctor_env(monkeypatch, "openai", model="gpt-5")
    monkeypatch.setenv("LLM_AUTH", "entra")
    assert cli.main(["doctor"]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] config" in out and "LLM_AUTH=entra" in out
    assert "[SKIP] model" in out


def test_doctor_warns_when_workload_identity_and_key(monkeypatch, tmp_path, capsys):
    key_file = tmp_path / "key"
    key_file.write_text("sk-test-secret\n")
    _doctor_env(monkeypatch, "foundry", endpoint="https://x.openai.azure.com/")
    monkeypatch.setenv("LLM_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(tmp_path / "token"))
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "[OK  ] config" in out
    assert out.count("[WARN] auth") == 1 and "LLM_AUTH=entra" in out
    assert "sk-test-secret" not in out


@pytest.mark.parametrize("provider,endpoint", [("anthropic", ""), ("openai", "")])
def test_doctor_no_auth_warning_on_a_public_api_with_workload_identity(
    monkeypatch, tmp_path, capsys, provider, endpoint
):
    key_file = tmp_path / "key"
    key_file.write_text("sk-test-secret\n")
    _doctor_env(monkeypatch, provider, endpoint)
    monkeypatch.setenv("LLM_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(tmp_path / "token"))
    assert cli.main(["doctor"]) == 0
    assert "[WARN] auth" not in capsys.readouterr().out


def test_doctor_logs_the_auth_warning_once(monkeypatch, tmp_path, capsys):
    key_file = tmp_path / "key"
    key_file.write_text("sk-test-secret\n")
    _doctor_env(monkeypatch, "foundry", endpoint="https://x.openai.azure.com/")
    monkeypatch.setenv("LLM_API_KEY_FILE", str(key_file))
    monkeypatch.setenv("AZURE_FEDERATED_TOKEN_FILE", str(tmp_path / "token"))
    cli.main(["doctor"])
    assert capsys.readouterr().err.count("Workload Identity is available") == 1


def _fake_kubectl(tmp_path, can_i_answer: str, can_i_exit: int) -> str:
    """kubectl stand-in: `version` succeeds, `auth can-i` answers like the real one
    (it exits 1 when the answer is "no")."""
    script = tmp_path / "kubectl"
    script.write_text(
        f"#!{sys.executable}\n"
        "import sys\n"
        "if sys.argv[1:3] == ['auth', 'can-i']:\n"
        f"    print({can_i_answer!r}); sys.exit({can_i_exit})\n"
        "print('Client Version: v1.33.0')\n"
    )
    script.chmod(0o755)
    return str(script)


def test_doctor_secrets_check_ok_when_rbac_says_no(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MCP_URL", "")
    monkeypatch.setenv("KUBECTL_PATH", _fake_kubectl(tmp_path, "no", 1))
    cli.main(["doctor"])
    assert "[OK  ] rbac secrets" in capsys.readouterr().out


def test_doctor_secrets_check_warns_when_rbac_says_yes(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MCP_URL", "")
    monkeypatch.setenv("KUBECTL_PATH", _fake_kubectl(tmp_path, "yes", 0))
    cli.main(["doctor"])
    assert "[WARN] rbac secrets" in capsys.readouterr().out


def test_memory_commands_on_an_empty_memory(capsys):
    assert cli.main(["findings"]) == 0
    assert "No open findings." in capsys.readouterr().out
    assert cli.main(["history"]) == 0
    assert "No runs recorded yet." in capsys.readouterr().out
    assert cli.main(["notes"]) == 0
    assert "No notes yet." in capsys.readouterr().out
    assert cli.main(["history", "7"]) == 1


def test_check_then_findings_and_history(scripted_model, capsys):
    scripted_model(answer(findings_answer("One problem.", [CRASH])))
    assert cli.main(["check"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Health check — my-cluster — ")
    assert "**1 new · 1 open · 0 resolved**" in out

    assert cli.main(["findings"]) == 0
    assert "crashloop/payments-dev/deployment/api" in capsys.readouterr().out

    assert cli.main(["history"]) == 0
    listing = capsys.readouterr().out
    assert "check" in listing and "ok" in listing

    assert cli.main(["history", "1"]) == 0
    assert "## New" in capsys.readouterr().out


def test_second_identical_check_posts_nothing(scripted_model, monkeypatch):
    monkeypatch.setenv("TEAMS_WEBHOOK_URL", "https://hook")
    posted = []

    async def fake_post(url, payload, timeout=30, transport=None):
        posted.append(payload)
        return True

    monkeypatch.setattr(cli, "post_to_teams", fake_post)
    scripted_model(answer(findings_answer("One problem.", [CRASH])))
    assert cli.main(["check", "--post"]) == 0
    scripted_model(answer(findings_answer("Same problem.", [CRASH])))
    assert cli.main(["check", "--post"]) == 0
    assert len(posted) == 1  # second run: nothing new or resolved


def test_check_post_without_webhook_only_warns(scripted_model, capsys):
    # cli.main() reconfigures logging to stderr (force=True), so read stderr, not caplog.
    scripted_model(answer(findings_answer("One problem.", [CRASH])))
    assert cli.main(["check", "--post"]) == 0
    assert "webhook is not configured" in capsys.readouterr().err


def test_failed_check_exits_1(scripted_model, capsys):
    scripted_model(answer("no json"), answer("still none"))
    assert cli.main(["check"]) == 1
    assert "Health check failed" in capsys.readouterr().out


def test_truncated_reply_fails_the_check_with_a_hint(scripted_model, capsys):
    # A reply cut off by the token limit must be a recorded failure, not a traceback.
    from k8s_servant.llm.types import ModelOutputTruncated

    scripted_model(ModelOutputTruncated("cut"))
    assert cli.main(["check"]) == 1
    err = capsys.readouterr().err
    assert "output limit" in err and "Traceback" not in err
    cli.main(["history"])
    assert "output limit" in capsys.readouterr().out  # recorded as a failed run


def test_truncated_reply_fails_ask_with_a_hint(scripted_model, capsys):
    from k8s_servant.llm.types import ModelOutputTruncated

    scripted_model(ModelOutputTruncated("cut"))
    assert cli.main(["ask", "why?"]) == 1
    assert "output limit" in capsys.readouterr().err


def test_report_command(scripted_model, capsys):
    scripted_model(answer(findings_answer("Fine.", [])), answer("All calm this morning."))
    assert cli.main(["report"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Morning report — my-cluster — ")
    assert "All calm this morning." in out


def test_ask_is_recorded_in_history(scripted_model, capsys):
    scripted_model(answer("Everything is fine."))
    assert cli.main(["ask", "is", "it", "fine?"]) == 0
    assert "Everything is fine." in capsys.readouterr().out
    assert cli.main(["history"]) == 0
    assert "ask" in capsys.readouterr().out


def test_bad_endpoint_is_a_configuration_error(monkeypatch, capsys):
    monkeypatch.setenv("LLM_PROVIDER", "foundry")
    monkeypatch.setenv("LLM_ENDPOINT", "http://insecure")
    monkeypatch.setenv("LLM_MODEL", "d")
    assert cli.main(["check"]) == 2
    assert "https://" in capsys.readouterr().err


def test_unwritable_data_dir_is_a_configuration_error(monkeypatch, tmp_path, capsys):
    # Final review: sqlite3.OperationalError is not an OSError; it used to crash with a traceback.
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)
    monkeypatch.setenv("DATA_DIR", str(locked))
    try:
        assert cli.main(["findings"]) == 2
    finally:
        locked.chmod(0o755)
    assert "Configuration error" in capsys.readouterr().err


def test_check_model_error_inside_mcp_session_is_explained_not_a_traceback(scripted_model, monkeypatch, capsys):
    # Seen on a large cluster: a 429 inside a session with MCP connected surfaced as an
    # ExceptionGroup traceback, because the MCP client's task group wrapped it.
    from contextlib import asynccontextmanager

    import anyio
    import httpx2
    import openai

    from k8s_servant import runtime

    class FailingChat:
        async def complete(self, messages, tools):
            raise openai.APIConnectionError(message="boom", request=httpx2.Request("POST", "https://x"))

    @asynccontextmanager
    async def agent_inside_task_group(*args, **kwargs):
        async with anyio.create_task_group(), runtime.open_agent(*args, **kwargs) as session:
            yield session

    scripted_model()
    monkeypatch.setattr(cli, "build_chat", lambda config: FailingChat())
    monkeypatch.setattr(cli, "open_agent", agent_inside_task_group)
    assert cli.main(["check"]) == 1
    err = capsys.readouterr().err
    assert "Model call failed: cannot reach LLM_ENDPOINT" in err
    assert "Traceback" not in err


def test_scheduled_checks_skip_the_model_when_nothing_changed(scripted_model):
    from k8s_servant.config import configure_app
    from k8s_servant.knowledge import Knowledge
    from k8s_servant.memory import Memory

    config = configure_app()
    memory = Memory(config["data_dir"])
    first = scripted_model(answer(findings_answer("One problem.", [CRASH])))
    asyncio.run(cli.check_and_post(config, Knowledge(), memory, first, post=False, scheduled=True))
    second = scripted_model()  # no replies: a model call would fail the test
    result = asyncio.run(cli.check_and_post(config, Knowledge(), memory, second, post=False, scheduled=True))
    assert second.calls == [] and result.ok and result.diff.open_count == 1
    # A manual check always asks the model.
    third = scripted_model(answer(findings_answer("One problem.", [CRASH])))
    asyncio.run(cli.check_and_post(config, Knowledge(), memory, third, post=False))
    assert len(third.calls) == 1


def test_scheduled_check_logs_the_redacted_model_error_not_the_provider_body(
    scripted_model, monkeypatch, caplog, capsys
):
    # OpenAI echoes the key in its 401 body; serve mode must not put it in the pod log.
    import logging

    import httpx2
    import openai

    from k8s_servant.config import configure_app
    from k8s_servant.knowledge import Knowledge
    from k8s_servant.memory import Memory
    from k8s_servant.scheduler import Scheduler

    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_ENDPOINT", "")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-secret")
    config = configure_app()
    memory = Memory(config["data_dir"])
    response = httpx2.Response(401, request=httpx2.Request("POST", "https://api.openai.com/v1/responses"))
    body = {"error": {"message": "Incorrect API key provided: sk-test-secret."}}
    error = openai.AuthenticationError("Incorrect API key provided: sk-test-secret.", response=response, body=body)
    chat = scripted_model(error)
    jobs = cli.serve_jobs(config, Knowledge(), memory, chat)
    scheduler = Scheduler(config, memory, *jobs)

    with caplog.at_level(logging.DEBUG):
        asyncio.run(scheduler._run_job("health check", jobs[0], asyncio.Event()))

    logged = caplog.text + capsys.readouterr().err
    assert "sk-test-secret" not in logged
    assert "check LLM_API_KEY_FILE" in logged
    assert not any(record.exc_info for record in caplog.records)
    # The failed run is still recorded, with the redacted reason.
    run = memory.runs(1)[0]
    assert run.kind == "check" and run.status != "ok" and "sk-test-secret" not in run.summary


def _fixture_scan(monkeypatch, flavor, fail_all=False):
    """Replace the cluster with a scan fixture; `fail_all` makes every input fail."""
    from fakes import fixture_fetch

    from k8s_servant.scan.inputs import INPUTS

    fail = {name: RuntimeError("connection refused") for name, _, _ in INPUTS} if fail_all else None
    real_scan = cli.scan

    async def fake_scan(fetch, config, cluster, memory=None, **kwargs):
        return await real_scan(fixture_fetch(flavor, fail=fail), config, cluster, memory, **kwargs)

    monkeypatch.setattr(cli, "scan", fake_scan)


def test_onboard_no_input_writes_the_cluster_file_only(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "aks")
    assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == str(tmp_path / "my-cluster.yaml")
    assert "TODO(onboard)" in (tmp_path / "my-cluster.yaml").read_text() and "questions left open" in err


def test_onboard_refuses_to_overwrite(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "aks")
    (tmp_path / "my-cluster.yaml").write_text("mine")
    assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(tmp_path)]) == 1
    assert "--force" in capsys.readouterr().err and (tmp_path / "my-cluster.yaml").read_text() == "mine"


def test_onboard_checks_existing_files_before_the_scan(monkeypatch, tmp_path, capsys):
    """The platform file counts too, even when the run would not write it."""
    called = []

    async def no_scan(*args, **kwargs):
        called.append(True)

    monkeypatch.setattr(cli, "scan", no_scan)
    (tmp_path / "knowledge-values.yaml").write_text("mine")
    assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(tmp_path)]) == 1
    assert "knowledge-values.yaml" in capsys.readouterr().err and not called


def test_onboard_force_overwrites(monkeypatch, tmp_path):
    _fixture_scan(monkeypatch, "aks")
    (tmp_path / "my-cluster.yaml").write_text("mine")
    assert cli.main(["onboard", "--no-input", "--no-model", "--force", "--out", str(tmp_path)]) == 0
    assert (tmp_path / "my-cluster.yaml").read_text() != "mine"


def test_onboard_markdown_format(monkeypatch, tmp_path):
    _fixture_scan(monkeypatch, "kind")
    out = tmp_path / "out"  # tmp_path also holds the fake kubectl and DATA_DIR
    assert cli.main(["onboard", "--no-input", "--no-model", "--format", "markdown", "--out", str(out)]) == 0
    assert {p.name for p in out.iterdir()} == {"cluster.md", "settings.env"}


def test_onboard_cluster_name_names_the_file(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "kind")
    assert cli.main(["onboard", "--no-input", "--no-model", "--cluster-name", "Prod EU", "--out", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == str(tmp_path / "prod-eu.yaml")
    assert 'CLUSTER_NAME: "Prod EU"' in (tmp_path / "prod-eu.yaml").read_text()


def test_onboard_without_a_model_still_writes(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "kind")
    assert cli.main(["onboard", "--no-input", "--out", str(tmp_path)]) == 0
    assert "without an overview" in capsys.readouterr().err


def test_onboard_writes_the_model_overview(scripted_model, monkeypatch, tmp_path):
    _fixture_scan(monkeypatch, "kind")
    chat = scripted_model(answer("A small test cluster."))
    assert cli.main(["onboard", "--no-input", "--out", str(tmp_path)]) == 0
    assert "A small test cluster." in (tmp_path / "my-cluster.yaml").read_text()
    assert len(chat.calls) == 1


def test_onboard_model_error_still_writes(scripted_model, monkeypatch, tmp_path, capsys):
    from k8s_servant.llm.types import ModelOutputTruncated

    _fixture_scan(monkeypatch, "kind")
    scripted_model(ModelOutputTruncated("cut"))
    assert cli.main(["onboard", "--no-input", "--out", str(tmp_path)]) == 0
    assert "without an overview" in capsys.readouterr().err
    assert "## Overview" not in (tmp_path / "my-cluster.yaml").read_text()


def test_onboard_unreachable_cluster_exits_1(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "kind", fail_all=True)
    assert cli.main(["onboard", "--no-input", "--out", str(tmp_path)]) == 1
    assert "Could not read the cluster" in capsys.readouterr().err
    assert not list(tmp_path.glob("*.yaml"))


def test_onboard_asks_when_run_in_a_terminal(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "aks")
    answers = iter(["y"])  # representative; every later question is skipped (EOF)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return next(answers, "")

    monkeypatch.setattr(cli, "terminal_ask", ask)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert cli.main(["onboard", "--no-model", "--out", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.split() == [str(tmp_path / "knowledge-values.yaml"), str(tmp_path / "my-cluster.yaml")]
    assert len(prompts) > 1 and any("outbound traffic" in p for p in prompts)
    assert "TODO(onboard): How does outbound traffic" in (tmp_path / "knowledge-values.yaml").read_text()


def test_onboard_confirmed_doubts_leave_fewer_todos(monkeypatch, tmp_path):
    _fixture_scan(monkeypatch, "aks")
    assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(tmp_path / "plain")]) == 0
    plain = (tmp_path / "plain" / "my-cluster.yaml").read_text()

    def ask(prompt):
        # Not representative; yes to every doubt; skip the open questions.
        return "n" if "representative" in prompt else "y" if "[y]es" in prompt else ""

    monkeypatch.setattr(cli, "terminal_ask", ask)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert cli.main(["onboard", "--no-model", "--out", str(tmp_path / "asked")]) == 0
    assert not (tmp_path / "asked" / "knowledge-values.yaml").exists()
    asked = (tmp_path / "asked" / "my-cluster.yaml").read_text()
    assert asked.count("TODO(onboard)") < plain.count("TODO(onboard)")
    assert "How does outbound traffic" in asked


def test_onboard_out_is_a_file_is_a_message(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "kind")
    out = tmp_path / "taken"
    out.write_text("a file, not a folder")
    assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(out)]) == 1
    err = capsys.readouterr().err
    assert f"Could not write the draft to {out}:" in err and "Traceback" not in err
    assert len([line for line in err.splitlines() if "Could not write" in line]) == 1


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="root ignores folder permissions")
def test_onboard_read_only_folder_is_a_message(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "kind")
    out = tmp_path / "ro"
    out.mkdir()
    out.chmod(0o500)
    try:
        assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(out)]) == 1
    finally:
        out.chmod(0o700)
    err = capsys.readouterr().err
    assert f"Could not write the draft to {out}: Permission denied" in err and "Traceback" not in err


def test_onboard_counts_the_statements_written(monkeypatch, tmp_path, capsys):
    import re

    _fixture_scan(monkeypatch, "aks")
    out = tmp_path / "out"
    assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(out)]) == 0
    text = (out / "my-cluster.yaml").read_text()
    bullets = [line.strip() for line in text.splitlines() if line.strip().startswith("- ")]
    stated = [line for line in bullets if not line.startswith("- TODO(onboard)")]
    n = int(re.search(r"(\d+) facts,", capsys.readouterr().err).group(1))
    assert n == len(stated) and n > 0


def test_onboard_model_error_never_shows_the_api_key(monkeypatch, tmp_path, capsys):
    import httpx2
    import openai

    _fixture_scan(monkeypatch, "kind")
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_MODEL", "gpt-5")
    monkeypatch.setenv("LLM_API_KEY", "sk-test-secret")
    response = httpx2.Response(401, request=httpx2.Request("POST", "https://api.openai.com/v1/responses"))
    body = {"error": {"message": "Incorrect API key provided: sk-test-secret."}}
    error = openai.AuthenticationError("Incorrect API key provided: sk-test-secret.", response=response, body=body)
    monkeypatch.setattr(cli, "build_chat", lambda config: ScriptedChat(error))
    out = tmp_path / "out"
    assert cli.main(["onboard", "--no-input", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    assert "sk-test-secret" not in captured.err + captured.out
    assert "without an overview" in captured.err
    assert "sk-test-secret" not in (out / "my-cluster.yaml").read_text()


def test_onboard_does_not_count_a_fact_left_out_of_the_file(monkeypatch, tmp_path, capsys):
    from datetime import UTC, datetime

    from k8s_servant.scan.facts import ScanResult, fact

    facts = [
        fact("platform/a", "platform", "Kubernetes v1.33.2", ("kubectl version",), ("version",)),
        fact("platform/b", "platform", "No proof for this", (), ()),  # dropped: a scanned fact needs evidence
    ]

    async def fake_scan(fetch, config, cluster, memory=None, **kwargs):
        return ScanResult(datetime(2026, 10, 21, tzinfo=UTC), cluster, facts, {}, True)

    monkeypatch.setattr(cli, "scan", fake_scan)
    assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(tmp_path / "out")]) == 0
    assert "1 facts," in capsys.readouterr().err


def test_onboard_end_of_input_stops_the_questions(monkeypatch, tmp_path):
    _fixture_scan(monkeypatch, "aks")
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return None  # stdin closed at once

    monkeypatch.setattr(cli, "terminal_ask", ask)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    assert cli.main(["onboard", "--no-model", "--out", str(tmp_path / "out")]) == 0
    assert len(prompts) == 1 and "representative" in prompts[0]


def test_onboard_show_prints_the_stored_scan(monkeypatch, capsys):
    memory = Memory(configure_app()["data_dir"])
    save_scan(memory, asyncio.run(scan(fixture_fetch("aks"), configure_app(), "my-cluster", memory)))
    assert cli.main(["onboard", "--show"]) == 0
    notes = yaml.safe_load(capsys.readouterr().out)["knowledge"]["clusterNotes"]
    assert "Managed service: AKS" in notes  # a platform-default fact is still shown


@pytest.mark.parametrize(
    "extra",
    [["--format", "markdown"], ["--out", "x"], ["--cluster-name", "c"], ["--force"], ["--no-input"], ["--no-model"]],
)
def test_onboard_show_takes_no_other_options(extra, capsys):
    assert cli.main(["onboard", "--show", *extra]) == 2
    assert "--show prints the stored scan; it takes no other options." in capsys.readouterr().err


def test_onboard_show_uses_the_placeholder_for_an_unset_cluster_name(capsys):
    memory = Memory(configure_app()["data_dir"])
    save_scan(memory, asyncio.run(scan(fixture_fetch("aks"), configure_app(), "unknown-cluster", memory)))
    assert cli.main(["onboard", "--show"]) == 0
    out = capsys.readouterr().out
    assert "TODO(onboard): Set the real cluster name." in out and "unknown-cluster" not in out


def test_onboard_show_without_a_stored_scan(capsys):
    assert cli.main(["onboard", "--show"]) == 1
    assert "No stored scan yet" in capsys.readouterr().err


def test_report_rescans_before_reporting(scripted_model, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_kubectl_fetch", lambda config: fixture_fetch("aks"))
    scripted_model(answer(findings_answer("Fine.", [])), answer("Calm."))
    assert cli.main(["report"]) == 0
    assert "- Since the last scan: First scan recorded" in capsys.readouterr().out
    assert load_scan(Memory(configure_app()["data_dir"])) is not None


def test_report_without_scan_before_report(scripted_model, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_kubectl_fetch", lambda config: fixture_fetch("aks"))
    monkeypatch.setenv("SCAN_BEFORE_REPORT", "false")
    scripted_model(answer(findings_answer("Fine.", [])), answer("Calm."))
    assert cli.main(["report"]) == 0
    assert "Since the last scan" not in capsys.readouterr().out
    assert load_scan(Memory(configure_app()["data_dir"])) is None
