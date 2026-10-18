"""The `k8s-servant` command line.

    k8s-servant [--values FILE ...] [--knowledge-dir DIR] [--log-level LEVEL] COMMAND

    ask "question"     ask the agent a question about the cluster
    check [--post]     run one health check; --post sends it to Teams (per TEAMS_POST_MODE)
    report [--post]    produce the morning report now; --post sends it to Teams
    serve              hourly checks + the morning report until stopped (the pod's main process)
    findings [--all]   open findings from memory (--all: resolved ones too)
    history [ID]       past runs, or the full report of one run
    notes              the agent's learned notes
    tools              the tools available right now (MCP or kubectl fallback)
    doctor             check configuration and connectivity, step by step

The same command works on a laptop (your kubeconfig and `az login`) and inside the
agent's pod (`kubectl exec -it deploy/k8s-servant -c agent -- k8s-servant ...`),
where it shares memory with the scheduler.

Exit codes: 0 ok, 1 something failed at runtime, 2 configuration error, 130 Ctrl-C.

Every command is a `cmd_<name>(args, config, knowledge, memory) -> int` coroutine:
the parsed arguments, the settings (config.configure_app()), the loaded knowledge and
the agent's memory; it returns the exit code. main() builds those once and handles
configuration errors and Ctrl-C for all commands.
"""

import argparse
import asyncio
import logging
import signal
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta

import yaml

from k8s_servant.config import configure_app, configure_logging, missing_model_settings
from k8s_servant.healthcheck import CheckResult, record_failed_run, run_health_check
from k8s_servant.knowledge import load_knowledge
from k8s_servant.llm import MODEL_ERRORS, build_chat, describe_model, model_error_text
from k8s_servant.llm.auth import resolve_model_auth
from k8s_servant.llm.types import Turn
from k8s_servant.memory import Memory
from k8s_servant.report import MorningReport, build_morning_report, format_age
from k8s_servant.runtime import open_agent, open_runtime
from k8s_servant.scheduler import Scheduler
from k8s_servant.teams import (
    check_card,
    morning_card,
    post_to_teams,
    resolve_webhook_url,
    should_post_check,
    should_post_report,
)
from k8s_servant.tools.kubectl_tool import run_kubectl
from k8s_servant.tools.prometheus_tool import check_ready

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Define the command-line interface."""
    parser = argparse.ArgumentParser(prog="k8s-servant", description="Read-only AI SRE agent for Kubernetes clusters.")
    parser.add_argument(
        "--values",
        action="append",
        default=[],
        metavar="FILE",
        help="Helm values file to read knowledge from (repeatable, later files win). "
        "Use the same files the cluster uses, e.g. examples/knowledge-values.yaml.",
    )
    parser.add_argument("--knowledge-dir", metavar="DIR", help="Knowledge folder (overrides KNOWLEDGE_DIR).")
    parser.add_argument("--log-level", metavar="LEVEL", help="DEBUG, INFO or WARNING (overrides LOG_LEVEL).")
    commands = parser.add_subparsers(dest="command", required=True, metavar="COMMAND")
    ask = commands.add_parser("ask", help="Ask the agent a question about the cluster.")
    ask.add_argument("question", nargs="+", help="The question; quotes are optional.")
    check = commands.add_parser("check", help="Run one health check and print the report.")
    check.add_argument("--post", action="store_true", help="Send it to Teams according to TEAMS_POST_MODE.")
    report = commands.add_parser("report", help="Produce the morning report now and print it.")
    report.add_argument("--post", action="store_true", help="Send it to Teams (unless TEAMS_POST_MODE=off).")
    commands.add_parser("serve", help="Run hourly checks and the morning report until stopped.")
    findings = commands.add_parser("findings", help="Show open findings from memory.")
    findings.add_argument("--all", action="store_true", help="Include resolved findings.")
    history = commands.add_parser("history", help="List past runs, or show the report of one run.")
    history.add_argument("id", nargs="?", type=int, help="Run id to show in full.")
    history.add_argument("--limit", type=int, default=20, help="How many runs to list (default 20).")
    commands.add_parser("notes", help="Show the agent's learned notes.")
    commands.add_parser("tools", help="List the tools available right now.")
    commands.add_parser("doctor", help="Check configuration and connectivity.")
    return parser


def _make_chat(config: dict):
    """Build the model client. Returns (chat, None), or (None, exit_code) after printing why not."""
    missing = missing_model_settings(config)
    if missing:
        print(f"Missing settings: {', '.join(missing)}. See docs/model-providers.md.", file=sys.stderr)
        return None, 2
    try:
        return build_chat(config), None
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return None, 2


async def _post(config: dict, wanted: bool, payload: dict, what: str) -> bool:
    """Send a card to Teams when wanted and configured; never raises."""
    if not wanted:
        logger.info("Not posting the %s to Teams (TEAMS_POST_MODE=%s)", what, config["teams_post_mode"])
        return False
    url = resolve_webhook_url(config)
    if not url:
        logger.warning(
            "Teams webhook is not configured (TEAMS_WEBHOOK_URL_FILE / TEAMS_WEBHOOK_URL); not posting the %s", what
        )
        return False
    return await post_to_teams(url, payload)


async def check_and_post(
    config: dict, knowledge, memory: Memory, chat, post: bool, scheduled: bool = False
) -> CheckResult:
    """One health check in a fresh session, posted to Teams per TEAMS_POST_MODE when `post`.

    Scheduled checks skip the model when nothing changed (CHECK_SKIP_UNCHANGED);
    manual ones always run in full.

    Args:
        config: Settings.
        knowledge: Loaded knowledge for the system prompt.
        memory: The agent's memory.
        chat: Model client (build_chat(), or a fake in tests).
        post: Send the result to Teams (subject to TEAMS_POST_MODE).
        scheduled: True for `serve`: the model may be skipped when nothing changed.

    Returns:
        The CheckResult (also stored in memory).

    Raises:
        MODEL_ERRORS: The model call failed (the run is recorded as failed first).
    """
    started = datetime.now(UTC)
    result: CheckResult | None = None
    error = None
    async with open_agent(config, knowledge, memory, chat=chat) as session:
        try:
            result = await run_health_check(
                session.agent,
                session.registry,
                memory,
                config["cluster_name"],
                allow_skip=scheduled and config["check_skip_unchanged"],
                full_interval=timedelta(hours=config["check_full_interval_hours"]),
                ignore_alerts=config["alerts_ignore_names"],
            )
        except MODEL_ERRORS as exc:
            record_failed_run(memory, "check", started, model_error_text(exc, config))
            error = exc
    # Raised only after the session is closed: inside it, the MCP client's task group
    # would wrap the error in an ExceptionGroup that callers do not expect.
    if error is not None:
        raise error
    if post:
        await _post(
            config,
            should_post_check(config["teams_post_mode"], result),
            check_card(config["cluster_name"], result),
            "health check",
        )
    return result


async def report_and_post(config: dict, knowledge, memory: Memory, chat, post: bool) -> MorningReport:
    """The morning report in a fresh session, posted to Teams when `post` (unless mode is off).

    Args:
        config: Settings.
        knowledge: Loaded knowledge for the system prompt.
        memory: The agent's memory.
        chat: Model client.
        post: Send the report to Teams.

    Returns:
        The MorningReport (also stored in memory). Never raises for a failed check
        or overview: the report then says what failed.
    """
    async with open_agent(config, knowledge, memory, chat=chat) as session:
        report = await build_morning_report(session, memory, config)
    if post:
        await _post(
            config,
            should_post_report(config["teams_post_mode"]),
            morning_card(config["cluster_name"], report),
            "morning report",
        )
    return report


async def cmd_ask(args, config: dict, knowledge, memory: Memory) -> int:
    """Answer one question and print the answer to stdout."""
    chat, code = _make_chat(config)
    if code is not None:
        return code
    question = " ".join(args.question)
    started = datetime.now(UTC)
    t0 = time.monotonic()
    async with open_agent(config, knowledge, memory, chat=chat) as session:
        surfaces = session.registry.surfaces()
        try:
            result = await session.agent.run(question)
        except MODEL_ERRORS as exc:
            print(f"Model call failed: {model_error_text(exc, config)}", file=sys.stderr)
            record_failed_run(memory, "ask", started, model_error_text(exc, config))
            return 1
    memory.record_run(
        "ask",
        started,
        time.monotonic() - t0,
        "ok",
        question[:500],
        f"# Question\n\n{question}\n\n# Answer\n\n{result.answer}\n",
    )
    print(result.answer or "(the model returned an empty answer)")
    limit_note = " - stopped at MAX_ITERATIONS" if result.hit_limit else ""
    print(
        f"\n-- {result.iterations} model calls, {result.tool_calls} tool calls, "
        f"tools: {', '.join(sorted(surfaces))}{limit_note}",
        file=sys.stderr,
    )
    return 0


async def cmd_check(args, config: dict, knowledge, memory: Memory) -> int:
    """Run one health check and print its report."""
    chat, code = _make_chat(config)
    if code is not None:
        return code
    try:
        result = await check_and_post(config, knowledge, memory, chat, post=args.post)
    except MODEL_ERRORS as exc:
        print(f"Model call failed: {model_error_text(exc, config)}", file=sys.stderr)
        return 1
    print(result.report_md)
    return 0 if result.ok else 1


async def cmd_report(args, config: dict, knowledge, memory: Memory) -> int:
    """Produce the morning report now and print it."""
    chat, code = _make_chat(config)
    if code is not None:
        return code
    report = await report_and_post(config, knowledge, memory, chat, post=args.post)
    print(report.report_md)
    return 0


async def cmd_serve(args, config: dict, knowledge, memory: Memory) -> int:
    """Run the scheduler until SIGTERM or Ctrl-C."""
    chat, code = _make_chat(config)
    if code is not None:
        return code
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)

    async def check_job():
        await check_and_post(config, knowledge, memory, chat, post=True, scheduled=True)

    async def morning_job():
        await report_and_post(config, knowledge, memory, chat, post=True)

    await Scheduler(config, memory, check_job, morning_job).run(stop)
    return 0


async def cmd_findings(args, config: dict, knowledge, memory: Memory) -> int:
    """List findings from memory."""
    findings = memory.all_findings() if args.all else memory.open_findings()
    if not findings:
        print("No findings recorded yet." if args.all else "No open findings.")
        return 0
    now = datetime.now(UTC)
    for f in findings:
        state = "open" if f.status == "open" else f"resolved {f.resolved_at:%Y-%m-%d %H:%M}"
        print(f"{f.severity:<8} {state:<22} {format_age(now - f.first_seen):>10}  {f.fingerprint}  {f.title}")
    return 0


async def cmd_history(args, config: dict, knowledge, memory: Memory) -> int:
    """List past runs, or print one run's report."""
    if args.id is not None:
        run = memory.run(args.id)
        if run is None:
            print(f"No run with id {args.id}.", file=sys.stderr)
            return 1
        print(run.report_md)
        return 0
    runs = memory.runs(args.limit)
    if not runs:
        print("No runs recorded yet.")
        return 0
    for r in runs:
        summary = r.summary.strip().splitlines()[0] if r.summary.strip() else ""
        print(
            f"{r.id:>5}  {r.started_at:%Y-%m-%d %H:%M}  {r.kind:<6} {r.status:<6} "
            f"new {r.new_count:<3} open {r.open_count:<3} resolved {r.resolved_count:<3} {summary[:70]}"
        )
    return 0


async def cmd_notes(args, config: dict, knowledge, memory: Memory) -> int:
    """Print the learned notes."""
    print(memory.notes().rstrip() or "No notes yet.")
    return 0


async def cmd_tools(args, config: dict, knowledge, memory: Memory) -> int:
    """Print the live tools grouped by surface, then the skills."""
    async with open_runtime(config, knowledge, memory=memory) as runtime:
        tools = runtime.registry.tools()
        mcp_available = runtime.mcp_available
    if not mcp_available:
        print("MCP server not reachable or disabled: the kubectl fallback is the only cluster tool.\n")
    width = max(len(t.name) for t in tools)
    for tool in tools:
        summary = tool.description.strip().splitlines()[0] if tool.description.strip() else ""
        print(f"{tool.surface:<10} {tool.name:<{width}}  {summary[:100]}")
    if knowledge.skills:
        print("\nSkills:")
        skill_width = max(len(name) for name in knowledge.skills)
        for name in sorted(knowledge.skills):
            print(f"  {name:<{skill_width}}  {knowledge.skills[name].source}")
    return 0


def _first_line(text: str) -> str:
    return text.strip().splitlines()[0] if text.strip() else ""


async def cmd_doctor(args, config: dict, knowledge, memory: Memory) -> int:
    """Check every dependency in turn and print one status line per check."""
    statuses: list[str] = []

    def report(status: str, check: str, detail: str) -> None:
        statuses.append(status)
        print(f"[{status:<4}] {check:<16} {detail}")

    # 1. Configuration
    missing = missing_model_settings(config)
    config_ok = False
    if missing:
        report("FAIL", "config", f"missing {', '.join(missing)} (see docs/model-providers.md)")
    else:
        try:
            # Resolve once: it validates the combination and logs the auto-mode warning.
            auth = resolve_model_auth(config)
        except ValueError as exc:
            report("FAIL", "config", str(exc))
        else:
            config_ok = True
            report("OK", "config", describe_model(config, auth))
            if auth.warning:
                report("WARN", "auth", auth.warning)

    # 2. Knowledge
    report(
        "OK" if knowledge.environment else "WARN",
        "knowledge",
        f"environment: {'yes' if knowledge.environment else 'no (the agent knows nothing site-specific)'}, "
        f"cluster notes: {'yes' if knowledge.cluster_notes else 'no'}, skills: {len(knowledge.skills)}",
    )

    # 3. Memory: write something, so a read-only volume shows up here.
    memory.set_meta("doctor_last_run", datetime.now(UTC).isoformat(timespec="seconds"))
    report(
        "OK",
        "memory",
        f"{memory.data_dir} writable: {len(memory.open_findings())} open findings, notes {len(memory.notes())} chars",
    )

    async with open_runtime(config, knowledge) as runtime:
        # 4. MCP
        mcp_count = sum(1 for t in runtime.registry.tools() if t.surface == "mcp")
        if not config["mcp_url"]:
            report("WARN", "mcp", "disabled (MCP_URL is empty); kubectl fallback only")
        elif mcp_count:
            report("OK", "mcp", f"{mcp_count} tools from {config['mcp_url']}")
        else:
            report("WARN", "mcp", f"not reachable at {config['mcp_url']}; kubectl fallback will be used")

        # 5. kubectl: reachability of the API server
        version = await run_kubectl(["version"], config["kubectl_path"], config["kubectl_timeout_seconds"])
        report("FAIL" if version.startswith("ERROR") else "OK", "kubectl", " | ".join(version.splitlines()[:3]))

        # 6. Secrets must not be readable in the cluster (RBAC). Locally, as an
        #    admin, "yes" is expected and only a warning.
        if not version.startswith("ERROR"):
            can_i = await run_kubectl(
                ["auth", "can-i", "get", "secrets", "-A"], config["kubectl_path"], config["kubectl_timeout_seconds"]
            )
            # `kubectl auth can-i` exits 1 when the answer is "no", so run_kubectl puts an
            # "ERROR: exited with code 1" line first; look for the answer on its own line.
            answers = {line.strip() for line in can_i.splitlines()}
            if "no" in answers:
                report("OK", "rbac secrets", "this identity cannot read Secrets")
            elif "yes" in answers:
                report(
                    "WARN",
                    "rbac secrets",
                    "this identity can read Secrets; fine on a laptop, must be 'no' in the cluster",
                )
            else:
                report("WARN", "rbac secrets", f"could not check ({_first_line(can_i)})")

    # 7. Prometheus and Alertmanager (optional)
    for name, key in (("prometheus", "prometheus_url"), ("alertmanager", "alertmanager_url")):
        url = config[key]
        if not url:
            report("WARN", name, f"not configured ({key.upper()}); the agent works without it")
            continue
        problem = await check_ready(url, config["prometheus_timeout_seconds"])
        report("OK" if problem is None else "FAIL", name, url if problem is None else f"{url}: {problem}")

    # 8. Teams (configuration only; doctor never posts)
    mode = config["teams_post_mode"]
    if mode == "off":
        report("OK", "teams", "posting disabled (TEAMS_POST_MODE=off)")
    elif resolve_webhook_url(config):
        report("OK", "teams", f"webhook configured, mode {mode} (doctor does not post)")
    else:
        report(
            "WARN",
            "teams",
            "no webhook (TEAMS_WEBHOOK_URL_FILE / TEAMS_WEBHOOK_URL); reports go to logs and history only",
        )

    # 9. Model: a tiny real completion proves endpoint, auth, deployment and network.
    if not config_ok:
        report("SKIP", "model", "configuration incomplete")
    else:
        try:
            chat = build_chat(config)
            started = time.monotonic()
            reply = await chat.complete([Turn("user", "Reply with the single word OK.")], [])
            report("OK", "model", f"answered {reply.content.strip()[:40]!r} in {time.monotonic() - started:.1f}s")
        except (*MODEL_ERRORS, ValueError) as exc:
            report("FAIL", "model", model_error_text(exc, config))

    return 1 if "FAIL" in statuses else 0


COMMANDS = {
    "ask": cmd_ask,
    "check": cmd_check,
    "report": cmd_report,
    "serve": cmd_serve,
    "findings": cmd_findings,
    "history": cmd_history,
    "notes": cmd_notes,
    "tools": cmd_tools,
    "doctor": cmd_doctor,
}


def main(argv: list[str] | None = None) -> int:
    """Entry point of the `k8s-servant` command."""
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    try:
        config = configure_app()
        if args.knowledge_dir:
            config["knowledge_dir"] = args.knowledge_dir
        knowledge = load_knowledge(config["knowledge_dir"], args.values)
    except (ValueError, OSError, yaml.YAMLError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    try:
        memory = Memory(config["data_dir"], config["notes_max_chars"])
    except (OSError, sqlite3.Error) as exc:
        # sqlite3.Error is not an OSError: it is what an unwritable folder (e.g. a
        # read-only volume) raises when the database is opened.
        print(f"Configuration error: cannot use DATA_DIR {config['data_dir']!r}: {exc}", file=sys.stderr)
        return 2
    try:
        return asyncio.run(COMMANDS[args.command](args, config, knowledge, memory))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
