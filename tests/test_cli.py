import sys

import pytest
from fakes import ScriptedChat, answer, findings_answer

from k8s_servant import cli

ENV_VARS = (
    "LLM_PROVIDER",
    "LLM_ENDPOINT",
    "LLM_MODEL",
    "LLM_API_KEY",
    "LLM_API_KEY_FILE",
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
    import asyncio

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
