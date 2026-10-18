from datetime import UTC, datetime

from fakes import ScriptedChat, answer, tool_call

from k8s_servant.agent import Agent, build_system_prompt
from k8s_servant.knowledge import Knowledge, parse_skill
from k8s_servant.llm.types import ToolCall, ToolSpec, Turn
from k8s_servant.tools.registry import Tool, ToolRegistry


def _registry(output="pod-a CrashLoopBackOff"):
    async def run(arguments):
        return output

    registry = ToolRegistry()
    registry.add(Tool("pods_list", "List pods", {"type": "object", "properties": {}}, run, "mcp"))
    return registry


def _agent(chat, registry, max_iterations=5, max_chars=1000):
    return Agent(chat, registry, "SYSTEM", max_iterations, max_chars)


async def test_direct_answer():
    chat = ScriptedChat(answer("All good"))
    result = await _agent(chat, _registry()).run("Is the cluster healthy?")
    assert (result.answer, result.iterations, result.tool_calls, result.hit_limit) == ("All good", 1, 0, False)
    assert chat.calls[0]["turns"][:2] == [Turn("system", "SYSTEM"), Turn("user", "Is the cluster healthy?")]
    assert chat.calls[0]["tools"] == [ToolSpec("pods_list", "List pods", {"type": "object", "properties": {}})]
    assert chat.calls[0]["use_tools"] is True


async def test_tool_call_then_answer():
    chat = ScriptedChat(tool_call("pods_list", {"namespace": "x"}), answer("pod-a is crashing"))
    result = await _agent(chat, _registry()).run("Any broken pods?")
    assert (result.answer, result.iterations, result.tool_calls) == ("pod-a is crashing", 2, 1)
    second = chat.calls[1]["turns"]
    assert second[2] == Turn("assistant", "", [ToolCall("c1", "pods_list", '{"namespace": "x"}')])
    assert second[3] == Turn("tool", "pod-a CrashLoopBackOff", tool_call_id="c1")


async def test_unknown_tool_is_reported_to_the_model():
    chat = ScriptedChat(tool_call("delete_everything", {}), answer("I cannot do that"))
    await _agent(chat, _registry()).run("x")
    assert chat.calls[1]["turns"][3].content.startswith("ERROR: unknown tool 'delete_everything'")


async def test_tool_output_is_truncated():
    chat = ScriptedChat(tool_call("pods_list", {}), answer("done"))
    await _agent(chat, _registry(output="x" * 5000), max_chars=300).run("x")
    content = chat.calls[1]["turns"][3].content
    assert len(content) < 500 and "characters omitted" in content


async def test_iteration_limit_asks_for_a_final_answer_without_tools():
    chat = ScriptedChat(tool_call("pods_list", {}, "c1"), tool_call("pods_list", {}, "c2"), answer("partial answer"))
    result = await _agent(chat, _registry(), max_iterations=2).run("x")
    assert (result.answer, result.tool_calls, result.hit_limit) == ("partial answer", 2, True)
    last = chat.calls[-1]
    assert last["use_tools"] is False and last["tools"]  # definitions still sent
    assert "limit" in last["turns"][-1].content


async def test_result_contains_the_whole_conversation():
    chat = ScriptedChat(tool_call("pods_list", {}), answer("done"))
    result = await _agent(chat, _registry()).run("q")
    assert [m.role for m in result.messages] == ["system", "user", "assistant", "tool", "assistant"]
    assert result.messages[-1] == Turn("assistant", "done")


async def test_follow_up_continues_the_conversation_without_tools():
    chat = ScriptedChat(answer("prose"), answer('{"findings": []}'))
    agent = _agent(chat, _registry())
    result = await agent.run("q")
    assert await agent.follow_up(result.messages, "Only the JSON, please") == '{"findings": []}'
    assert chat.calls[-1]["use_tools"] is False and chat.calls[-1]["tools"]
    assert chat.calls[-1]["turns"][-1] == Turn("user", "Only the JSON, please")
    assert chat.calls[-1]["turns"][-2] == Turn("assistant", "prose")


def test_system_prompt_contains_knowledge_and_surfaces():
    knowledge = Knowledge(
        environment="Consul servers are VMs.",
        cluster_notes="Dev cluster.",
        skills={"s": parse_skill("s", "---\ndescription: Do S.\n---\nbody", "t")},
    )
    prompt = build_system_prompt(
        "my-cluster", knowledge, {"kubectl", "knowledge"}, datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
    )
    assert '"my-cluster"' in prompt and "2026-10-09 08:00 UTC" in prompt
    assert "Consul servers are VMs." in prompt and "Dev cluster." in prompt and "- s: Do S." in prompt
    assert "MCP server is not available" in prompt


def test_system_prompt_without_environment():
    prompt = build_system_prompt("c", Knowledge(), {"mcp", "kubectl"})
    assert "No environment description was provided" in prompt
    assert "MCP server is not available" not in prompt


def test_system_prompt_includes_learned_notes():
    prompt = build_system_prompt(
        "c", Knowledge(), {"mcp", "memory", "prometheus"}, notes="- 2026-10-12: job x fails on Sundays"
    )
    assert "## Learned notes" in prompt and "job x fails on Sundays" in prompt
    assert "save_note" in prompt and "Prometheus" in prompt


def test_no_notes_section_without_notes():
    assert "## Learned notes" not in build_system_prompt("c", Knowledge(), {"mcp", "memory"})


def test_system_prompt_asks_for_autonomous_investigation():
    # First real run: the model ended with "If you want, I can do that next" instead
    # of following the firing alerts it had found.
    prompt = build_system_prompt("c", Knowledge(), {"mcp", "kubectl", "cluster"})
    assert "Never end with an offer" in prompt
    assert "cluster_problems" in prompt  # the compact summaries are named as the starting point


def test_system_prompt_asks_to_copy_names_exactly():
    # Second real run: PDB quick-search-dev/quicksearch-dev was reported as
    # quick-search-dev/quick-search-dev, a different, healthy object.
    assert "character for character" in build_system_prompt("c", Knowledge(), {"kubectl"})
