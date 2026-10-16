"""The kubectl fallback tool.

kubernetes-mcp-server is the preferred way to read the cluster. When it is not
available (sidecar down, not deployed, a local run without it) the agent still
works through this tool, which runs the real `kubectl` binary.

Safety comes in layers:
  1. RBAC. The agent's ServiceAccount can only read, and cannot read Secrets.
     This is the real security boundary (see docs/security.md).
  2. This guard. Only read verbs, no Secrets, and only *allow-listed* flags.
     An allow-list (not a deny-list) matters here: kubectl has many flags that
     change identity or target server (sending its token elsewhere), read local
     files (e.g. `-o go-template-file=<ServiceAccount token>`), write files or
     never return. Anything not known to be harmless is refused. Values of
     flags are parsed the way kubectl parses them (`-n x`, `-nx`, `-n=x`,
     `--namespace=x`), so a value cannot be smuggled in as a "different" flag.
  3. No shell. kubectl is started with an argument list, so `;`, `|` or `$(...)`
     inside an argument are just characters, never commands.
"""

import asyncio
import logging
import shlex

from k8s_servant.tools.registry import Tool

logger = logging.getLogger(__name__)

# Verbs that only read. `auth` is handled separately: only `auth can-i` is allowed.
READ_VERBS = {
    "get",
    "describe",
    "logs",
    "top",
    "events",
    "api-resources",
    "api-versions",
    "explain",
    "version",
    "cluster-info",
}

# Every flag the agent may use, mapped to whether it takes a value. All other
# flags are refused. Grouped by the verbs that use them.
ALLOWED_FLAGS = {
    # selection and output (get, describe, events, top, api-resources)
    "-n": True,
    "--namespace": True,
    "-A": False,
    "--all-namespaces": False,
    "-l": True,
    "--selector": True,
    "--field-selector": True,
    "-o": True,
    "--output": True,
    "--sort-by": True,
    "--no-headers": False,
    "--show-labels": False,
    "-L": True,
    "--label-columns": True,
    "--show-kind": False,
    "--ignore-not-found": False,
    "--chunk-size": True,
    "--request-timeout": True,
    "--show-managed-fields": False,
    "--subresource": True,
    # logs (no --follow: it never returns)
    "-c": True,
    "--container": True,
    "-p": False,
    "--previous": False,
    "--tail": True,
    "--since": True,
    "--since-time": True,
    "--timestamps": False,
    "--all-containers": False,
    "--prefix": False,
    "--limit-bytes": True,
    "--max-log-requests": True,
    # top
    "--containers": False,
    "--sum": False,
    "--use-protocol-buffers": False,
    # events
    "--for": True,
    "--types": True,
    # api-resources, explain, auth can-i, version
    "--namespaced": False,
    "--verbs": True,
    "--api-group": True,
    "--api-version": True,
    "--recursive": False,
    "--list": False,
    "--client": False,
}

# Output formats that only change how the API response is printed. Formats that
# load a template from a local file (`*-file`) or run Go templates are refused.
SAFE_OUTPUTS = {"wide", "yaml", "json", "name"}
SAFE_OUTPUT_PREFIXES = ("jsonpath=", "jsonpath-as-json=", "custom-columns=")

# Sub-commands that would dump very large amounts of data.
FORBIDDEN_SUBCOMMANDS = {"dump"}

_DESCRIPTION = (
    "Run a read-only kubectl command against the cluster and return its output. "
    "Pass the arguments without the word 'kubectl', e.g. "
    '["get", "pods", "-n", "kube-system", "-o", "wide"]. '
    "Allowed verbs: get, describe, logs, top, events, api-resources, api-versions, "
    "explain, version, cluster-info, auth can-i. Secrets cannot be read. "
    "Only common read flags are accepted: -n, -A, -l, --field-selector, --sort-by, "
    "-o wide|yaml|json|name|jsonpath=...|custom-columns=..., and for logs -c, -p, --tail, --since. "
    "Keep output small: filter with -n, -l or --field-selector and use --tail with logs."
)


def _is_secret_reference(arg: str) -> bool:
    """True if a positional argument names the Secret resource type.

    Handles `secret`, `secrets`, `secret/name`, `secrets.v1.` and comma lists like
    `pods,secrets`. Other types that merely contain the word, such as
    `externalsecrets`, are allowed. This is deliberately conservative: an object
    literally named "secret" is refused too.
    """
    for part in arg.lower().split(","):
        resource = part.split("/", 1)[0]
        if resource in ("secret", "secrets") or resource.startswith(("secret.", "secrets.")):
            return True
    return False


def normalize_args(raw) -> list[str]:
    """Accept the arguments as a list or as one string, and drop a leading `kubectl`.

    Raises:
        ValueError: If `raw` is neither a string nor a list of strings.
    """
    if isinstance(raw, str):
        args = shlex.split(raw)
    elif isinstance(raw, list) and all(isinstance(a, str) for a in raw):
        args = list(raw)
    else:
        raise ValueError('args must be a list of strings, e.g. ["get", "pods", "-A"]')
    if args and args[0] == "kubectl":
        args = args[1:]
    return args


def _split_flag(arg: str) -> tuple[str, str | None]:
    """Split a flag argument into (flag name, attached value or None).

    Mirrors kubectl's parser: `--name=value`, `--name`, `-x`, `-x=value` and
    `-xvalue` (a short flag directly followed by its value).
    """
    if arg.startswith("--"):
        name, equals, value = arg.partition("=")
        return name, (value if equals else None)
    name, rest = arg[:2], arg[2:]
    return name, (rest.removeprefix("=") if rest else None)


def _safe_output(value: str) -> bool:
    """True for output formats that only change how the API response is printed."""
    return value in SAFE_OUTPUTS or value.startswith(SAFE_OUTPUT_PREFIXES)


def check_kubectl_args(args: list[str]) -> str | None:
    """Return why the arguments are refused, or None if they are allowed."""
    if not args:
        return "no kubectl arguments given"
    verb = args[0]
    if verb == "auth":
        if len(args) < 2 or args[1] != "can-i":
            return "only 'auth can-i' is allowed from the auth command"
        position = 2
    elif verb in READ_VERBS:
        position = 1
    else:
        allowed = ", ".join(sorted(READ_VERBS))
        return f"verb {verb!r} is not allowed; read-only verbs are: {allowed}, auth can-i"
    while position < len(args):
        arg = args[position]
        if arg == "--":
            return "'--' is not allowed"
        if arg.startswith("-"):
            name, value = _split_flag(arg)
            if name not in ALLOWED_FLAGS:
                return f"flag {name!r} is not allowed"
            if not ALLOWED_FLAGS[name]:
                # Boolean flag. kubectl reads `-Axyz` as the flags -A -x -y -z, so
                # anything glued to a short boolean flag could hide another flag
                # (`-Afalse` is `-A -f alse`, a local file). Only an explicit
                # `=true` / `=false` is accepted (`-A=false`, `--all-namespaces=true`).
                explicit = "=" in arg and value is not None and value.lower() in ("true", "false")
                if value is not None and not explicit:
                    return f"flag {name!r} takes no value (write short flags separately)"
            else:
                if value is None:  # the value is the next argument
                    position += 1
                    if position >= len(args):
                        return f"flag {name!r} needs a value"
                    value = args[position]
                if name in ("-o", "--output") and not _safe_output(value):
                    return (
                        f"output format {value!r} is not allowed; use wide, yaml, json, name, "
                        "jsonpath=..., jsonpath-as-json=... or custom-columns=..."
                    )
        elif arg in FORBIDDEN_SUBCOMMANDS:
            return f"{arg!r} is not allowed"
        elif _is_secret_reference(arg):
            return "reading Secrets is not allowed"
        position += 1
    return None


async def run_kubectl(args: list[str], kubectl_path: str, timeout_seconds: int) -> str:
    """Run kubectl with the given arguments and return its output as text.

    This function does NOT check the arguments; callers that take input from the
    model must call check_kubectl_args() first (build_kubectl_tool does).

    Args:
        args: kubectl arguments without the leading "kubectl".
        kubectl_path: kubectl binary.
        timeout_seconds: The process is killed after this.

    Returns:
        stdout, plus stderr under a "[stderr]" marker; "ERROR: ..." first when
        kubectl failed, timed out or is missing.
    """
    logger.info("kubectl %s", shlex.join(args))
    try:
        process = await asyncio.create_subprocess_exec(
            kubectl_path,
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return f"ERROR: kubectl binary not found at {kubectl_path!r}"
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError:
        process.kill()
        await process.wait()
        return f"ERROR: kubectl timed out after {timeout_seconds}s; narrow the query"
    out = stdout.decode("utf-8", errors="replace").strip()
    err = stderr.decode("utf-8", errors="replace").strip()
    parts = []
    if process.returncode != 0:
        parts.append(f"ERROR: kubectl exited with code {process.returncode}")
    if out:
        parts.append(out)
    if err:
        parts.append(f"[stderr]\n{err}")
    return "\n".join(parts) if parts else "(no output)"


def build_kubectl_tool(kubectl_path: str, timeout_seconds: int, mcp_available: bool) -> Tool:
    """Create the `kubectl` tool.

    Args:
        kubectl_path: kubectl binary to run.
        timeout_seconds: Maximum duration of one call.
        mcp_available: When True the description tells the model to prefer the
            MCP tools and use this one only as a fallback.
    """
    prefix = "FALLBACK: use only when none of the Kubernetes MCP tools can answer. " if mcp_available else ""

    async def run(arguments: dict) -> str:
        """The kubectl tool: normalise and check the arguments, then run kubectl."""
        try:
            args = normalize_args(arguments.get("args"))
        except ValueError as exc:
            return f"ERROR: {exc}"
        reason = check_kubectl_args(args)
        if reason:
            logger.warning("Refused kubectl %s: %s", shlex.join(args), reason)
            return f"ERROR: refused: {reason}"
        return await run_kubectl(args, kubectl_path, timeout_seconds)

    return Tool(
        name="kubectl",
        description=prefix + _DESCRIPTION,
        parameters={
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "kubectl arguments, without the leading 'kubectl'",
                }
            },
            "required": ["args"],
        },
        run=run,
        surface="kubectl",
    )
