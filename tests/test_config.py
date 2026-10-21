import pytest

from k8s_servant.config import DEFAULT_KNOWLEDGE_DIR, configure_app, missing_model_settings


def test_defaults():
    config = configure_app({})
    assert config["mcp_url"] == "http://localhost:8080/mcp"
    assert config["kubectl_path"] == "kubectl"
    assert config["max_iterations"] == 25
    assert config["tool_output_max_chars"] == 8000
    assert config["llm_token_scope"] == "https://ai.azure.com/.default"
    assert config["knowledge_dir"] == str(DEFAULT_KNOWLEDGE_DIR)
    assert DEFAULT_KNOWLEDGE_DIR.parts[-2:] == ("k8s-servant", "knowledge")


def test_values_are_read_and_trimmed():
    config = configure_app({"LLM_ENDPOINT": " https://x.openai.azure.com/ ", "MAX_ITERATIONS": " 5 ", "MCP_URL": ""})
    assert config["llm_endpoint"] == "https://x.openai.azure.com/"
    assert config["max_iterations"] == 5
    assert config["mcp_url"] == ""


@pytest.mark.parametrize("raw", ["abc", "1.5", "0", "-3", ""])
def test_bad_integer_names_the_variable(raw):
    with pytest.raises(ValueError, match="MAX_ITERATIONS"):
        configure_app({"MAX_ITERATIONS": raw})


def test_mcp_timeout_setting():
    assert configure_app({})["mcp_timeout_seconds"] == 60
    assert configure_app({"MCP_TIMEOUT_SECONDS": "15"})["mcp_timeout_seconds"] == 15


def test_stage2_defaults():
    config = configure_app({})
    assert config["data_dir"] == ".data"
    assert config["notes_max_chars"] == 8000
    assert config["prometheus_url"] == "" and config["alertmanager_url"] == ""
    assert config["prometheus_timeout_seconds"] == 30
    assert config["teams_webhook_url"] == "" and config["teams_webhook_url_file"] == ""
    assert config["teams_post_mode"] == "changes"
    assert config["check_interval_minutes"] == 60
    assert config["morning_report_enabled"] is True
    assert config["morning_report_time"] == "08:00"
    assert config["morning_report_timezone"] == "UTC"
    assert config["morning_report_weekdays_only"] is True
    assert config["heartbeat_file"] == ""


def test_urls_lose_their_trailing_slash():
    config = configure_app({"PROMETHEUS_URL": "http://prom:9090/", "ALERTMANAGER_URL": " http://am:9093/ "})
    assert config["prometheus_url"] == "http://prom:9090"
    assert config["alertmanager_url"] == "http://am:9093"


def test_booleans_choices_and_time_are_normalised():
    config = configure_app(
        {
            "MORNING_REPORT_ENABLED": "no",
            "MORNING_REPORT_WEEKDAYS_ONLY": "0",
            "MORNING_REPORT_TIME": "7:30",
            "TEAMS_POST_MODE": "Always",
            "MORNING_REPORT_TIMEZONE": "UTC",
        }
    )
    assert config["morning_report_enabled"] is False
    assert config["morning_report_weekdays_only"] is False
    assert config["morning_report_time"] == "07:30"
    assert config["teams_post_mode"] == "always"
    assert config["morning_report_timezone"] == "UTC"


@pytest.mark.parametrize(
    "name, raw",
    [
        ("TEAMS_POST_MODE", "sometimes"),
        ("MORNING_REPORT_ENABLED", "maybe"),
        ("MORNING_REPORT_TIME", "8am"),
        ("MORNING_REPORT_TIME", "25:00"),
        ("MORNING_REPORT_TIMEZONE", "Mars/Base"),
        ("MORNING_REPORT_TIMEZONE", ""),
        ("CHECK_INTERVAL_MINUTES", "0"),
        ("NOTES_MAX_CHARS", "lots"),
    ],
)
def test_invalid_settings_name_the_variable(name, raw):
    with pytest.raises(ValueError, match=name):
        configure_app({name: raw})


def test_reasoning_effort_is_read_trimmed_and_may_be_empty():
    assert configure_app({"LLM_REASONING_EFFORT": " High "})["llm_reasoning_effort"] == "high"
    # Empty = do not send the parameter (models without reasoning reject it).
    assert configure_app({"LLM_PROVIDER": "foundry", "LLM_REASONING_EFFORT": ""})["llm_reasoning_effort"] == ""


def test_bad_reasoning_effort_names_the_variable():
    with pytest.raises(ValueError, match="LLM_REASONING_EFFORT"):
        configure_app({"LLM_REASONING_EFFORT": "extreme"})


@pytest.mark.parametrize("effort", ["xhigh", "max"])
def test_reasoning_effort_accepts_the_newer_levels(effort):
    assert configure_app({"LLM_REASONING_EFFORT": effort})["llm_reasoning_effort"] == effort


def test_old_foundry_setting_fails_with_the_new_name():
    with pytest.raises(ValueError, match=r"FOUNDRY_DEPLOYMENT.*LLM_MODEL"):
        configure_app({"FOUNDRY_DEPLOYMENT": "x"})


@pytest.mark.parametrize(
    "provider,effort",
    [("foundry", "medium"), ("openai", "medium"), ("anthropic", "medium"), ("ollama", ""), ("openai-compatible", "")],
)
def test_reasoning_effort_default_depends_on_provider(provider, effort):
    assert configure_app({"LLM_PROVIDER": provider})["llm_reasoning_effort"] == effort


def test_reasoning_effort_default_is_empty_without_a_provider():
    assert configure_app({})["llm_reasoning_effort"] == ""


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match=r"LLM_PROVIDER.*foundry"):
        configure_app({"LLM_PROVIDER": "bedrock"})


def test_provider_default_endpoint_is_applied():
    assert configure_app({"LLM_PROVIDER": "ollama"})["llm_endpoint"] == "http://localhost:11434/v1"
    assert configure_app({"LLM_PROVIDER": "ollama", "LLM_ENDPOINT": "http://x:1/v1"})["llm_endpoint"] == "http://x:1/v1"


@pytest.mark.parametrize(
    "env,missing",
    [
        ({}, ["LLM_PROVIDER", "LLM_MODEL"]),
        ({"LLM_PROVIDER": "foundry", "LLM_MODEL": "m"}, ["LLM_ENDPOINT"]),
        ({"LLM_PROVIDER": "anthropic", "LLM_MODEL": "m"}, []),
    ],
)
def test_missing_model_settings(env, missing):
    assert missing_model_settings(configure_app(env)) == missing


def test_workload_identity_detected():
    assert configure_app({})["workload_identity"] is False
    assert configure_app({"AZURE_FEDERATED_TOKEN_FILE": "/var/run/token"})["workload_identity"] is True


def test_events_exclude_reasons():
    assert configure_app({})["events_exclude_reasons"] == ("PolicyViolation",)
    assert configure_app({"EVENTS_EXCLUDE_REASONS": " PolicyViolation, PolicyApplied ,"})["events_exclude_reasons"] == (
        "PolicyViolation",
        "PolicyApplied",
    )
    assert configure_app({"EVENTS_EXCLUDE_REASONS": ""})["events_exclude_reasons"] == ()


def test_bad_events_exclude_reason_names_the_variable():
    # The reasons end up in a field selector; a comma or "=" could change its meaning.
    with pytest.raises(ValueError, match="EVENTS_EXCLUDE_REASONS"):
        configure_app({"EVENTS_EXCLUDE_REASONS": "type!=Warning"})


def test_alerts_ignore_names():
    assert configure_app({})["alerts_ignore_names"] == ("Watchdog", "InfoInhibitor")
    assert configure_app({"ALERTS_IGNORE_NAMES": ""})["alerts_ignore_names"] == ()


def test_skip_unchanged_settings():
    config = configure_app({})
    assert config["check_skip_unchanged"] is True and config["check_full_interval_hours"] == 6
    config = configure_app({"CHECK_SKIP_UNCHANGED": "false", "CHECK_FULL_INTERVAL_HOURS": "2"})
    assert config["check_skip_unchanged"] is False and config["check_full_interval_hours"] == 2


def test_model_retries_are_logged_but_http_noise_is_not():
    # serve on a large cluster: a model call hung ~8 min (timeouts and silent SDK retries).
    import logging

    from k8s_servant.config import configure_logging

    configure_logging("INFO")
    assert logging.getLogger("openai").level == logging.INFO  # only logs "Retrying request ..."
    assert logging.getLogger("httpx2").level == logging.WARNING


def test_api_key_file_wins_over_the_variable(tmp_path):
    # In the cluster the key is a mounted Secret file, so it never sits in the environment.
    key_file = tmp_path / "api-key"
    key_file.write_text(" from-file\n")
    config = configure_app({"LLM_API_KEY": "from-env", "LLM_API_KEY_FILE": str(key_file)})
    assert config["llm_api_key"] == "from-file"


def test_api_key_variable_is_used_without_a_file():
    assert configure_app({"LLM_API_KEY": " k "})["llm_api_key"] == "k"


def test_unreadable_api_key_file_is_an_error_that_names_only_the_variable(tmp_path):
    # Falling back to Entra ID silently would hide the mistake; the message must not
    # contain the key, only the setting's name.
    with pytest.raises(ValueError, match="LLM_API_KEY_FILE"):
        configure_app({"LLM_API_KEY_FILE": str(tmp_path / "missing")})


def test_empty_api_key_file_is_an_error(tmp_path):
    # An empty Secret value would otherwise fall back to Entra ID and fail confusingly.
    key_file = tmp_path / "api-key"
    key_file.write_text("  \n")
    with pytest.raises(ValueError, match="LLM_API_KEY_FILE"):
        configure_app({"LLM_API_KEY_FILE": str(key_file)})


def test_key_source_names_the_file(tmp_path):
    key_file = tmp_path / "key"
    key_file.write_text("secret\n")
    config = configure_app({"LLM_API_KEY": "from-env", "LLM_API_KEY_FILE": str(key_file)})
    assert config["llm_api_key_source"] == f"file {key_file}"
    assert "secret" not in config["llm_api_key_source"]


def test_key_source_names_the_variable_or_is_empty():
    assert configure_app({"LLM_API_KEY": "k"})["llm_api_key_source"] == "LLM_API_KEY"
    assert configure_app({})["llm_api_key_source"] == ""


def test_llm_defaults():
    config = configure_app({})
    assert (config["llm_auth"], config["llm_timeout_seconds"], config["llm_max_output_tokens"]) == ("auto", 120, 32000)
    assert config["llm_provider"] == ""
    with pytest.raises(ValueError, match="LLM_AUTH"):
        configure_app({"LLM_AUTH": "magic"})


def test_scan_before_report_defaults_on():
    assert configure_app({})["scan_before_report"] is True
    assert configure_app({"SCAN_BEFORE_REPORT": "false"})["scan_before_report"] is False
