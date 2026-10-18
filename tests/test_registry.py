from k8s_servant.llm.types import ToolSpec
from k8s_servant.tools.registry import Tool, ToolRegistry, truncate_output


async def _echo(arguments):
    return f"echo {arguments}"


async def _boom(arguments):
    raise RuntimeError("kaput")


def _tool(name, run=_echo, surface="test"):
    return Tool(name, f"{name} tool", {"type": "object", "properties": {}}, run, surface)


def test_specs_are_neutral_tool_specs():
    registry = ToolRegistry()
    registry.add(_tool("pods_list"))
    assert registry.specs() == [ToolSpec("pods_list", "pods_list tool", {"type": "object", "properties": {}})]


def test_first_tool_wins_and_invalid_names_are_skipped():
    registry = ToolRegistry()
    assert registry.add(_tool("x", surface="mcp"))
    assert not registry.add(_tool("x", surface="kubectl"))
    assert not registry.add(_tool("bad name!"))
    assert [t.surface for t in registry.tools()] == ["mcp"]
    assert registry.surfaces() == {"mcp"}


async def test_run_passes_parsed_arguments():
    registry = ToolRegistry()
    registry.add(_tool("x"))
    assert await registry.run("x", '{"a": 1}') == "echo {'a': 1}"
    assert await registry.run("x", "") == "echo {}"


async def test_run_never_raises():
    registry = ToolRegistry()
    registry.add(_tool("x"))
    registry.add(_tool("boom", run=_boom))
    assert (await registry.run("nope", "{}")).startswith("ERROR: unknown tool 'nope'")
    assert "not valid JSON" in await registry.run("x", "{not json")
    assert "must be a JSON object" in await registry.run("x", "[1, 2]")
    assert await registry.run("boom", "{}") == "ERROR: tool 'boom' failed: RuntimeError: kaput"


def test_truncate_output_keeps_head_and_tail():
    text = "H" * 100 + "M" * 1000 + "T" * 50
    out = truncate_output(text, 150)
    assert out.startswith("H" * 100)
    assert out.endswith("T" * 50)
    assert "1000 characters omitted" in out
    assert truncate_output("short", 150) == "short"
