from datetime import UTC, datetime

from fakes import ScriptedChat, answer, findings_answer

from k8s_servant.agent import Agent
from k8s_servant.findings import Finding
from k8s_servant.healthcheck import build_check_prompt, record_failed_run, run_check
from k8s_servant.memory import Memory
from k8s_servant.tools.registry import ToolRegistry

T0 = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
CRASH = {
    "fingerprint": "crashloop/payments-dev/Deployment/api",
    "severity": "critical",
    "title": "api crash-loops",
    "detail": "Exit code 1 since 05:40 UTC.\nFix the DB URL.",
}


def _agent(chat):
    return Agent(chat, ToolRegistry(), "SYSTEM", 5, 10_000)


async def test_check_records_findings_and_renders_a_report(tmp_path):
    memory = Memory(tmp_path)
    chat = ScriptedChat(answer(findings_answer("One problem.", [CRASH])))
    result = await run_check(_agent(chat), memory, "my-cluster", clock=lambda: T0)
    assert result.ok and result.summary == "One problem."
    assert [f.fingerprint for f in result.diff.new] == ["crashloop/payments-dev/deployment/api"]
    assert result.report_md.startswith("# Health check — my-cluster — 2026-10-12 06:00 UTC")
    assert "**1 new · 1 open · 0 resolved**" in result.report_md
    assert "## New" in result.report_md and "[CRITICAL] api crash-loops" in result.report_md
    assert "  Fix the DB URL." in result.report_md  # detail lines indented under the item
    run = memory.run(result.run_id)
    assert (run.kind, run.status, run.new_count, run.open_count) == ("check", "ok", 1, 1)
    assert "cluster-health-check" in chat.calls[0]["turns"][1].content


async def test_open_findings_are_given_to_the_model_and_resolved_when_gone(tmp_path):
    memory = Memory(tmp_path)
    memory.apply_check([Finding("pending/web/deployment/web", "warning", "web pending", "")], now=T0)
    chat = ScriptedChat(answer(findings_answer("All good now.", [])))
    result = await run_check(_agent(chat), memory, "my-cluster", clock=lambda: T0)
    assert "`pending/web/deployment/web`" in chat.calls[0]["turns"][1].content
    assert [f.fingerprint for f in result.diff.resolved] == ["pending/web/deployment/web"]
    assert "## Resolved" in result.report_md and "No problems found." not in result.report_md
    assert memory.open_findings() == []


def test_prompt_without_open_findings():
    prompt = build_check_prompt([])
    assert "(none)" in prompt and "```json" in prompt and "crashloop" in prompt


async def test_missing_json_gets_one_follow_up(tmp_path):
    memory = Memory(tmp_path)
    chat = ScriptedChat(answer("I looked around and all is fine."), answer(findings_answer("All fine.", [])))
    result = await run_check(_agent(chat), memory, "my-cluster", clock=lambda: T0)
    assert result.ok and result.summary == "All fine."
    assert chat.calls[1]["use_tools"] is False
    assert "JSON" in chat.calls[1]["turns"][-1].content
    assert "No problems found." in result.report_md


async def test_second_bad_answer_fails_and_leaves_memory_untouched(tmp_path):
    memory = Memory(tmp_path)
    memory.apply_check([Finding("a/x/deployment/y", "warning", "t", "")], now=T0)
    chat = ScriptedChat(answer("no json"), answer("still no json"))
    result = await run_check(_agent(chat), memory, "my-cluster", clock=lambda: T0)
    assert not result.ok and "json" in result.error.lower()
    assert [f.fingerprint for f in memory.open_findings()] == ["a/x/deployment/y"]
    assert memory.run(result.run_id).status == "failed"
    assert "memory was not changed" in result.report_md


def test_record_failed_run(tmp_path):
    memory = Memory(tmp_path)
    run = memory.run(record_failed_run(memory, "check", T0, "HTTP 401 authentication failed"))
    assert (run.kind, run.status) == ("check", "failed")
    assert run.summary.startswith("failed: HTTP 401")


def test_disruption_budgets_have_their_own_category():
    from k8s_servant.healthcheck import CATEGORIES, build_check_prompt

    assert "pdb" in CATEGORIES
    assert "pdb" in build_check_prompt([])


# --- skipping the model when the sweep is unchanged ---------------------------------

from datetime import timedelta  # noqa: E402

from k8s_servant.healthcheck import run_health_check  # noqa: E402
from k8s_servant.tools.registry import Tool  # noqa: E402

FULL_EVERY = timedelta(hours=6)


def _registry(outputs: dict[str, str]) -> ToolRegistry:
    """A registry whose summary tools return the given (mutable) outputs."""
    registry = ToolRegistry()
    for name in outputs:

        async def run(arguments, name=name):
            return outputs[name]

        registry.add(Tool(name, "d", {"type": "object", "properties": {}}, run, "cluster"))
    return registry


def _sweep(*problems: str, stamp: str = "12:00") -> str:
    lines = [
        f"Cluster problem sweep, all namespaces, 2026-10-12 {stamp} UTC",
        "Checked: 15 nodes, 1252 pods",
        f"Problems found: {len(problems)}",
        "## PodDisruptionBudgets",
        *problems,
    ]
    return "\n".join(lines)


async def _run(memory, registry, chat, at, allow_skip=True):
    agent = Agent(chat, registry, "SYSTEM", 5, 10_000)
    return await run_health_check(
        agent,
        registry,
        memory,
        "my-cluster",
        allow_skip=allow_skip,
        full_interval=FULL_EVERY,
        ignore_alerts=("Watchdog", "InfoInhibitor"),
        clock=lambda: at,
    )


async def test_unchanged_sweep_skips_the_model(tmp_path):
    memory = Memory(tmp_path)
    outputs = {
        "cluster_problems": _sweep("- PodDisruptionBudget q/orphan: matches no pods"),
        "alertmanager_alerts": "KubePdbNotEnoughHealthyPods [warning] namespace=q since 2026-09-23T18:53:13Z: x",
    }
    registry = _registry(outputs)
    first = await _run(memory, registry, ScriptedChat(answer(findings_answer("PDB.", [CRASH]))), T0)
    assert first.ok and len(first.diff.new) == 1

    # One hour later: same problems and alerts (only the sweep's own timestamp differs).
    outputs["cluster_problems"] = _sweep("- PodDisruptionBudget q/orphan: matches no pods", stamp="13:00")
    chat = ScriptedChat()  # any model call would fail: it has no replies
    second = await _run(memory, registry, chat, T0 + timedelta(hours=1))
    assert chat.calls == []
    assert second.ok and not second.diff.changed
    assert [f.fingerprint for f in second.diff.ongoing] == ["crashloop/payments-dev/deployment/api"]
    assert "Unchanged since the full check at 2026-10-12 06:00 UTC" in second.report_md
    run = memory.run(second.run_id)
    assert (run.kind, run.status, run.open_count) == ("check", "ok", 1)


async def test_changed_sweep_or_alerts_run_the_model(tmp_path):
    memory = Memory(tmp_path)
    outputs = {"cluster_problems": _sweep(), "alertmanager_alerts": "(no firing alerts)"}
    registry = _registry(outputs)
    await _run(memory, registry, ScriptedChat(answer(findings_answer("Fine.", []))), T0)
    outputs["alertmanager_alerts"] = "KubeNodeNotReady [warning] node=n1 since 2026-10-12T06:30:00Z: n1"
    chat = ScriptedChat(answer(findings_answer("Node.", [])))
    await _run(memory, registry, chat, T0 + timedelta(hours=1))
    assert len(chat.calls) == 1


async def test_full_check_at_least_every_interval_and_when_skipping_is_off(tmp_path):
    memory = Memory(tmp_path)
    registry = _registry({"cluster_problems": _sweep()})
    await _run(memory, registry, ScriptedChat(answer(findings_answer("Fine.", []))), T0)
    late = ScriptedChat(answer(findings_answer("Fine.", [])))
    await _run(memory, registry, late, T0 + FULL_EVERY)
    assert len(late.calls) == 1
    manual = ScriptedChat(answer(findings_answer("Fine.", [])))
    await _run(memory, registry, manual, T0 + FULL_EVERY + timedelta(minutes=5), allow_skip=False)
    assert len(manual.calls) == 1


async def test_no_skip_after_a_failed_check_or_an_incomplete_sweep(tmp_path):
    memory = Memory(tmp_path)
    outputs = {"cluster_problems": _sweep()}
    registry = _registry(outputs)
    # A check whose findings JSON is broken twice does not count as a full check.
    await _run(memory, registry, ScriptedChat(answer("no json"), answer("still none")), T0)
    chat = ScriptedChat(answer(findings_answer("Fine.", [])))
    await _run(memory, registry, chat, T0 + timedelta(hours=1))
    assert len(chat.calls) == 1
    # A sweep that could not read everything is no proof that nothing changed.
    outputs["cluster_problems"] = _sweep() + "\n\nCould not check jobs: RuntimeError: forbidden"
    await _run(memory, registry, ScriptedChat(answer(findings_answer("Fine.", []))), T0 + timedelta(hours=2))
    chat = ScriptedChat(answer(findings_answer("Fine.", [])))
    await _run(memory, registry, chat, T0 + timedelta(hours=3))
    assert len(chat.calls) == 1


async def test_without_the_sweep_tool_every_check_is_full(tmp_path):
    memory = Memory(tmp_path)
    for hour in range(2):
        chat = ScriptedChat(answer(findings_answer("Fine.", [])))
        await _run(memory, ToolRegistry(), chat, T0 + timedelta(hours=hour))
        assert len(chat.calls) == 1


async def test_flapping_always_on_alerts_and_alert_texts_do_not_force_a_full_check(tmp_path):
    # a large cluster: InfoInhibitor re-fires every few minutes with a new start time, so the
    # alert list never looked the same twice and no check was ever skipped.
    memory = Memory(tmp_path)
    outputs = {
        "cluster_problems": _sweep(),
        "alertmanager_alerts": "\n".join(
            [
                "KubeCPUOvercommit [warning] since 2026-10-12T04:00:00Z: CPU requests are 93% of capacity",
                "InfoInhibitor [none] namespace=a since 2026-10-12T11:47:52Z: Info-level alert inhibition.",
            ]
        ),
    }
    registry = _registry(outputs)
    await _run(memory, registry, ScriptedChat(answer(findings_answer("Fine.", []))), T0)
    outputs["alertmanager_alerts"] = "\n".join(
        [
            "KubeCPUOvercommit [warning] since 2026-10-12T04:00:00Z: CPU requests are 94% of capacity",
            "InfoInhibitor [none] namespace=b since 2026-10-12T12:19:52Z: Info-level alert inhibition.",
        ]
    )
    chat = ScriptedChat()
    result = await _run(memory, registry, chat, T0 + timedelta(hours=1))
    assert chat.calls == [] and "Unchanged" in result.summary
