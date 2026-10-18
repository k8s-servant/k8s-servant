from k8s_servant.llm.types import ModelOutputTruncated, ToolCall, Turn, log_usage


def test_turn_defaults_are_independent():
    a, b = Turn("assistant"), Turn("assistant")
    a.tool_calls.append(ToolCall("c1", "t", "{}"))
    assert b.tool_calls == [] and (a.content, a.tool_call_id, a.provider_state) == ("", "", None)


def test_log_usage_keeps_the_established_wording(caplog):
    caplog.set_level("INFO")
    log_usage(1200, 1024, 300, 200, 2.5)
    assert "model call: 1200 prompt (1024 cached) + 300 completion tokens (200 reasoning) in 2.5s" in caplog.text


def test_log_usage_leaves_out_reasoning_the_provider_does_not_report(caplog):
    caplog.set_level("INFO")
    log_usage(1200, 900, 300, None, 2.5)
    assert "model call: 1200 prompt (900 cached) + 300 completion tokens in 2.5s" in caplog.text


def test_truncated_is_an_exception():
    assert issubclass(ModelOutputTruncated, Exception)
