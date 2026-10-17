import pytest

from k8s_servant.config import DEFAULT_KNOWLEDGE_DIR, configure_app, missing_model_settings


def test_defaults():
    config = configure_app({})
    assert config["mcp_url"] == "http://localhost:8080/mcp"
    assert config["kubectl_path"] == "kubectl"
    assert config["max_iterations"] == 25
    assert config["tool_output_max_chars"] == 8000
    assert config["foundry_token_scope"] == "https://ai.azure.com/.default"
    assert config["knowledge_dir"] == str(DEFAULT_KNOWLEDGE_DIR)
    assert DEFAULT_KNOWLEDGE_DIR.parts[-2:] == ("k8s-servant", "knowledge")


def test_values_are_read_and_trimmed():
    config = configure_app(
        {"FOUNDRY_ENDPOINT": " https://x.openai.azure.com/ ", "MAX_ITERATIONS": " 5 ", "MCP_URL": ""}
    )
    assert config["foundry_endpoint"] == "https://x.openai.azure.com/"
    assert config["max_iterations"] == 5
    assert config["mcp_url"] == ""


@pytest.mark.parametrize("raw", ["abc", "1.5", "0", "-3", ""])
def test_bad_integer_names_the_variable(raw):
    with pytest.raises(ValueError, match="MAX_ITERATIONS"):
        configure_app({"MAX_ITERATIONS": raw})


def test_missing_model_settings():
    assert missing_model_settings(configure_app({})) == ["FOUNDRY_ENDPOINT", "FOUNDRY_DEPLOYMENT"]
    full = configure_app({"FOUNDRY_ENDPOINT": "https://x", "FOUNDRY_DEPLOYMENT": "m"})
    assert missing_model_settings(full) == []


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


def test_reasoning_effort_defaults_to_medium():
    # gpt-5.4-mini reasons not at all when the request does not ask for it (measured
    # on the first real run: 0 reasoning tokens, shallow 3-call investigations).
    assert configure_app({})["foundry_reasoning_effort"] == "medium"
    assert configure_app({"FOUNDRY_REASONING_EFFORT": " High "})["foundry_reasoning_effort"] == "high"
    # Empty = do not send the parameter (models without reasoning reject it).
    assert configure_app({"FOUNDRY_REASONING_EFFORT": ""})["foundry_reasoning_effort"] == ""


def test_bad_reasoning_effort_names_the_variable():
    with pytest.raises(ValueError, match="FOUNDRY_REASONING_EFFORT"):
        configure_app({"FOUNDRY_REASONING_EFFORT": "max"})


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
    config = configure_app({"FOUNDRY_API_KEY": "from-env", "FOUNDRY_API_KEY_FILE": str(key_file)})
    assert config["foundry_api_key"] == "from-file"


def test_api_key_variable_is_used_without_a_file():
    assert configure_app({"FOUNDRY_API_KEY": " k "})["foundry_api_key"] == "k"


def test_unreadable_api_key_file_is_an_error_that_names_only_the_variable(tmp_path):
    # Falling back to Entra ID silently would hide the mistake; the message must not
    # contain the key, only the setting's name.
    with pytest.raises(ValueError, match="FOUNDRY_API_KEY_FILE"):
        configure_app({"FOUNDRY_API_KEY_FILE": str(tmp_path / "missing")})
