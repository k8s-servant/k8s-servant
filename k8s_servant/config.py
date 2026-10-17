"""Application settings.

Every setting comes from an environment variable, so the same code runs on a
laptop (variables exported in the shell, see .env.example) and in the cluster
(variables come from the Helm chart's ConfigMap via envFrom). configure_app()
returns one plain dict; each key has a comment explaining what it controls and
why the default was chosen. Invalid values fail immediately with a message that
names the variable, instead of surfacing later as a confusing error.
"""

import logging
import os
import re
import sys
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Built-in skills ship inside the Helm chart folder (charts/k8s-servant/knowledge). When
# the package runs from a git checkout (local development) this path points at
# them, so local runs and the cluster use the same skill files. In the container
# KNOWLEDGE_DIR is set explicitly to the mounted ConfigMap.
DEFAULT_KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "charts" / "k8s-servant" / "knowledge"


def configure_logging(level: str | None = None) -> None:
    """Configure stdlib logging for the whole process.

    Logs go to stderr so that stdout carries only the agent's answer; this lets
    `k8s-servant ask ... > answer.md` work as expected.

    Args:
        level: Log level name (DEBUG, INFO, ...). Falls back to the LOG_LEVEL
            environment variable, then INFO.
    """
    level_name = (level or os.environ.get("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        force=True,
    )
    # Third-party libraries log every HTTP request at INFO; keep them to warnings.
    for noisy in ("azure", "httpx", "httpx2", "httpcore", "mcp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # The OpenAI SDK logs only its retries at INFO ("Retrying request in 8 s"). Without
    # them a model call that times out and retries looks like a silent hang.
    logging.getLogger("openai").setLevel(logging.INFO)


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    """Read a positive integer setting; fail with a message that names the variable."""
    raw = env.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"Environment variable {name} must be an integer, got {raw!r}") from None
    if value <= 0:
        raise ValueError(f"Environment variable {name} must be greater than 0, got {value}")
    return value


def _bool(env: Mapping[str, str], name: str, default: bool) -> bool:
    """Read a true/false setting (also accepts 1/0, yes/no, on/off)."""
    raw = env.get(name, "true" if default else "false").strip().lower()
    if raw in ("true", "1", "yes", "on"):
        return True
    if raw in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"Environment variable {name} must be true or false, got {raw!r}")


def _choice(env: Mapping[str, str], name: str, default: str, choices: tuple[str, ...]) -> str:
    """Read a setting that must be one of a few words (case-insensitive)."""
    raw = env.get(name, default).strip().lower()
    if raw not in choices:
        raise ValueError(f"Environment variable {name} must be one of {', '.join(choices)}, got {raw!r}")
    return raw


def _name_list(env: Mapping[str, str], name: str, default: str) -> tuple[str, ...]:
    """Read a comma-separated list of plain names (letters and digits only)."""
    names = tuple(part.strip() for part in env.get(name, default).split(",") if part.strip())
    for item in names:
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", item):
            raise ValueError(f"Environment variable {name} must list names like PolicyViolation, got {item!r}")
    return names


def _time_of_day(env: Mapping[str, str], name: str, default: str) -> str:
    """Read a HH:MM time of day and return it normalised (7:30 -> 07:30)."""
    raw = env.get(name, default).strip()
    try:
        parsed = datetime.strptime(raw, "%H:%M")
    except ValueError:
        raise ValueError(f"Environment variable {name} must be a time like 08:00, got {raw!r}") from None
    return parsed.strftime("%H:%M")


def _timezone(env: Mapping[str, str], name: str, default: str) -> str:
    """Read an IANA time zone name (e.g. Europe/Vilnius) and check that it exists."""
    raw = env.get(name, default).strip()
    try:
        ZoneInfo(raw)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"Environment variable {name} must be a time zone like Europe/Vilnius, got {raw!r}") from None
    return raw


def configure_app(env: Mapping[str, str] | None = None) -> dict:
    """Build the application settings dict from environment variables.

    Args:
        env: Mapping to read from. Defaults to os.environ; tests pass a plain dict.

    Returns:
        Dict of settings. Keys are snake_case versions of the variable names.

    Raises:
        ValueError: If a setting has an invalid value (the message names it).
    """
    env = os.environ if env is None else env
    return {
        # --- Identity ---
        # Name used in answers, reports and Teams cards. A friendly cluster name
        # cannot be discovered reliably from inside the cluster, so it is configured.
        "cluster_name": env.get("CLUSTER_NAME", "unknown-cluster").strip(),
        # --- MS Foundry model (see docs/foundry-setup.md) ---
        # Resource endpoint, e.g. https://<resource>.openai.azure.com/ . The client
        # appends /openai/v1/ when it is missing.
        "foundry_endpoint": env.get("FOUNDRY_ENDPOINT", "").strip(),
        # Name of the model *deployment* in Foundry (not the model name).
        "foundry_deployment": env.get("FOUNDRY_DEPLOYMENT", "").strip(),
        # API key, for local experiments only. Empty = Entra ID token (az login
        # locally, Workload Identity in the cluster), which is the recommended way.
        "foundry_api_key": env.get("FOUNDRY_API_KEY", "").strip(),
        # OAuth scope requested for the Entra ID token.
        "foundry_token_scope": env.get("FOUNDRY_TOKEN_SCOPE", "https://ai.azure.com/.default").strip(),
        # Per-request timeout; reasoning models can take a while on large contexts.
        "foundry_timeout_seconds": _int(env, "FOUNDRY_TIMEOUT_SECONDS", 120),
        # How hard a reasoning model (gpt-5.x) thinks before answering. Without it
        # gpt-5.4-mini does not reason at all, which made investigations shallow.
        # Empty = do not send the parameter (for models that reject it).
        "foundry_reasoning_effort": _choice(
            env, "FOUNDRY_REASONING_EFFORT", "medium", ("", "none", "minimal", "low", "medium", "high")
        ),
        # --- Kubernetes access ---
        # kubernetes-mcp-server endpoint (streamable HTTP). In the pod the server is a
        # sidecar on localhost. Empty string = MCP disabled, kubectl only.
        "mcp_url": env.get("MCP_URL", "http://localhost:8080/mcp").strip(),
        # One MCP tool call may not take longer than this. Without a limit a stalled
        # server would hang every call for minutes. Logs of busy pods can be slow, so
        # the default is higher than kubectl's.
        "mcp_timeout_seconds": _int(env, "MCP_TIMEOUT_SECONDS", 60),
        # kubectl binary used by the fallback tool.
        "kubectl_path": env.get("KUBECTL_PATH", "kubectl").strip(),
        # One kubectl call may not take longer than this.
        "kubectl_timeout_seconds": _int(env, "KUBECTL_TIMEOUT_SECONDS", 30),
        # Warning event reasons the warning_events tool hides unless asked. Policy
        # scanners (Kyverno background scans) emit thousands of PolicyViolation events
        # that say nothing about the cluster's health. Empty = hide nothing.
        "events_exclude_reasons": _name_list(env, "EVENTS_EXCLUDE_REASONS", "PolicyViolation"),
        # Alerts that fire all the time by design (kube-prometheus-stack: Watchdog
        # proves the pipeline works, InfoInhibitor mutes info alerts and re-fires every
        # few minutes). Left out of the morning report and of the "anything changed?"
        # comparison of scheduled checks. Empty = use every firing alert.
        "alerts_ignore_names": _name_list(env, "ALERTS_IGNORE_NAMES", "Watchdog,InfoInhibitor"),
        # --- Agent loop ---
        # Upper bound on model round-trips per question; protects against loops.
        "max_iterations": _int(env, "MAX_ITERATIONS", 25),
        # Tool output longer than this is cut (head + tail kept) before it reaches
        # the model, so one huge `get pods -A` cannot fill the context window. Every
        # later model call resends it, so it drives the token cost: two 20k log reads
        # added ~24k tokens to each following call on a large cluster.
        "tool_output_max_chars": _int(env, "TOOL_OUTPUT_MAX_CHARS", 8000),
        # --- Knowledge ---
        # Folder with environment.md, cluster.md and skills (docs/knowledge-and-skills.md).
        "knowledge_dir": env.get("KNOWLEDGE_DIR", "").strip() or str(DEFAULT_KNOWLEDGE_DIR),
        # --- Memory (docs/memory.md) ---
        # Folder for memory.db (findings, run history) and notes.md. In the cluster
        # this is the PersistentVolume mounted at /data; locally ./.data.
        "data_dir": env.get("DATA_DIR", "").strip() or ".data",
        # Only the newest learned notes up to this size go into the system prompt,
        # so a growing notes file cannot crowd out the investigation.
        "notes_max_chars": _int(env, "NOTES_MAX_CHARS", 8000),
        # --- Prometheus / Alertmanager (optional; tools exist only when set) ---
        # In the cluster e.g. http://kube-prometheus-stack-prometheus.kube-prometheus-stack.svc:9090
        "prometheus_url": env.get("PROMETHEUS_URL", "").strip().rstrip("/"),
        # e.g. http://kube-prometheus-stack-alertmanager.kube-prometheus-stack.svc:9093
        "alertmanager_url": env.get("ALERTMANAGER_URL", "").strip().rstrip("/"),
        # One Prometheus or Alertmanager request may not take longer than this.
        "prometheus_timeout_seconds": _int(env, "PROMETHEUS_TIMEOUT_SECONDS", 30),
        # --- MS Teams (docs: README, Teams section) ---
        # Workflows webhook URL. It is a credential: in the cluster it comes from a
        # Secret mounted as a file (TEAMS_WEBHOOK_URL_FILE), which wins when set.
        "teams_webhook_url": env.get("TEAMS_WEBHOOK_URL", "").strip(),
        "teams_webhook_url_file": env.get("TEAMS_WEBHOOK_URL_FILE", "").strip(),
        # changes: post a health check only when something is new or resolved (no
        # hourly noise); always: post every check; off: never post.
        "teams_post_mode": _choice(env, "TEAMS_POST_MODE", "changes", ("changes", "always", "off")),
        # --- Scheduling (`k8s-servant serve`) ---
        # Minutes between scheduled health checks.
        "check_interval_minutes": _int(env, "CHECK_INTERVAL_MINUTES", 60),
        # Scheduled checks skip the model when the cluster summary (problems and firing
        # alerts) is identical to the last full check: most hours nothing changes, and
        # a full check costs 150k-225k tokens. Manual `check` and the morning report
        # always run in full.
        "check_skip_unchanged": _bool(env, "CHECK_SKIP_UNCHANGED", True),
        # Even when nothing changed, run a full check at least this often: the summary
        # cannot see everything (Flux, node usage, events).
        "check_full_interval_hours": _int(env, "CHECK_FULL_INTERVAL_HOURS", 6),
        # A scheduled check or report that runs longer than this is cancelled, so one
        # stuck run (slow model, hanging tool) cannot block the scheduler for hours.
        "job_timeout_minutes": _int(env, "JOB_TIMEOUT_MINUTES", 30),
        # The morning report: posted once a day even when nothing changed.
        "morning_report_enabled": _bool(env, "MORNING_REPORT_ENABLED", True),
        # Local time of day for the morning report, in MORNING_REPORT_TIMEZONE.
        "morning_report_time": _time_of_day(env, "MORNING_REPORT_TIME", "08:00"),
        # Time zone of MORNING_REPORT_TIME; handles summer time correctly.
        "morning_report_timezone": _timezone(env, "MORNING_REPORT_TIMEZONE", "UTC"),
        # Skip Saturdays and Sundays.
        "morning_report_weekdays_only": _bool(env, "MORNING_REPORT_WEEKDAYS_ONLY", True),
        # File touched on every scheduler loop for the liveness probe. Empty = off;
        # the Helm chart sets /tmp/heartbeat.
        "heartbeat_file": env.get("HEARTBEAT_FILE", "").strip(),
    }


def missing_model_settings(config: dict) -> list[str]:
    """Return the names of required model variables that are not set."""
    missing = []
    if not config["foundry_endpoint"]:
        missing.append("FOUNDRY_ENDPOINT")
    if not config["foundry_deployment"]:
        missing.append("FOUNDRY_DEPLOYMENT")
    return missing
