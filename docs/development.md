# Development guide

How to work on the code: where things are, the rules every change follows, and
step-by-step recipes for the most common changes. Read [architecture.md](architecture.md)
first for the big picture, and [code-walkthrough.md](code-walkthrough.md) for a guided
tour of how the code works; [AGENTS.md](https://github.com/k8s-servant/k8s-servant/blob/main/AGENTS.md) holds the same conventions in
short form.

## Setup

```bash
python3.14 -m venv .venv && .venv/bin/pip install -e '.[dev,azure]'
.venv/bin/pytest                    # the whole suite, ~10 s, no cluster and no model needed
set -a; source .env; set +a         # for manual runs against a real cluster and model
```

If `.venv/bin/...` fails with "bad interpreter" after a Homebrew update, recreate the
venv (see [troubleshooting.md](troubleshooting.md#local-setup)).

## Code map

| If you want to change ... | Look at |
|---|---|
| A setting | `config.py` (and the table in `docs/configuration.md`, see below) |
| How the model is called (endpoint, auth, reasoning, token log) | `llm/` |
| The system prompt or the tool-calling loop | `agent.py` |
| Which tools a session gets, and in which order | `runtime.py` |
| What counts as "unhealthy" in the sweep | `tools/cluster_tools.py` |
| What kubectl may do | `tools/kubectl_tool.py` (allow-list) |
| Prometheus / Alertmanager access | `tools/prometheus_tool.py` |
| The health check, fingerprints, skipping unchanged checks | `healthcheck.py`, `findings.py` |
| What is remembered | `memory.py` |
| The morning report | `report.py` |
| Teams cards | `teams.py` |
| The scheduler | `scheduler.py` |
| A CLI command | `cli.py` |
| What the agent knows about the platform | `examples/knowledge-values.yaml` (example), `charts/k8s-servant/knowledge/skills/` (built-in skills) |

## Rules for every change

- **Read-only, always.** No code path may change cluster objects or read Secrets. A
    new tool reads; if it runs kubectl, it uses fixed arguments or goes through
    `check_kubectl_args()`.
- **The kubectl guard is an allow-list.** Add a flag only if it cannot change the
    identity or target server, read or write local files, or block. Never switch to a
    deny-list ([security.md](security.md#why-the-kubectl-guard-is-an-allow-list)).
- **Model text never becomes a command-line flag.** Validate every argument the model
    passes (see `_check_namespace()` in `cluster_tools.py`).
- **No shell.** `asyncio.create_subprocess_exec` with an argument list.
- **stdout carries only the answer**; logs go to stderr.
- **Settings only from environment variables**, through `config.configure_app()`, each
    with a comment that says why it exists.
- **A failed or unparseable health check never changes the findings in memory.**
- **The Teams webhook URL is a credential.** Never log it, not even inside an error.
- **Times are UTC**, except the morning report's schedule and title.
- **Built-in skills stay generic**: no site names, no write commands
    (`tests/test_builtin_skills.py`). Site facts go into the environment description.
- **Test first.** Write the failing test, watch it fail for the right reason, then the
    code. One test file per module.
- **Document as you go.** Every module starts with a docstring that explains what and
    why; every public function and every function longer than 15 lines has a Google-style
    docstring (`tests/test_docs.py` enforces this). Comments explain intent, not syntax.
- **Conventional commit messages and PR titles** (`feat:`, `fix:`, `docs:`, `refactor:`,
    `test:`); release-please writes `CHANGELOG.md` from them, so never edit it by hand.

## Tests

- `tests/fakes.py` holds the shared test doubles:
    - `ScriptedChat(*replies)`: a fake model that returns the replies in order (an
        exception instance is raised instead) and records every request in `.calls`.
    - `answer(text)`, `tool_call(name, args)`: model replies.
    - `findings_answer(summary, findings)`: a health-check answer with the JSON block.
- MCP is tested with an in-process `mcp.server.MCPServer`; HTTP with
    `httpx2.MockTransport`; kubectl-based tools with a fake `fetch` function or, in CLI
    tests, a fake `kubectl` script (CLI tests must never read a real cluster).
- `cli.main()` reconfigures logging to stderr, so CLI tests read stderr with `capsys`,
    not `caplog`.
- `tests/test_docs.py` checks the documentation: every setting in the
    `docs/configuration.md` tables, links between docs, docstrings.
- There is **no automated test of answer quality** yet. After changing the prompt, a
    skill, the model or the summary tools, run `ask` and `check` against a real cluster
    and compare with what you find by hand (`kubectl`).

## Recipes

### Add a setting

1. Add it to `configure_app()` in `config.py` with a comment, using the helpers
      (`_int`, `_bool`, `_choice`, `_name_list`, ...) so a bad value names the variable.
2. Test it in `tests/test_config.py` (default, a valid value, an invalid value).
3. Add a row to the right table in `docs/configuration.md`. `tests/test_docs.py` fails until you do.
4. Add a commented line to `.env.example`.
5. If it has a sensible per-cluster value, add it to `appConfig` in `charts/k8s-servant/values.yaml`
      (with a comment) or to the environment files.

### Add a check to `cluster_problems`

1. Write a pure function `xyz_problems(items: list[dict]) -> list[str]` in
      `tools/cluster_tools.py` that returns Markdown lines `- Kind namespace/name: what is wrong`.
      Report only real problems; say in the docstring what is deliberately not reported.
2. Test it in `tests/test_cluster_tools.py` with hand-written minimal JSON, including
      a healthy object that must not be reported.
3. Add the resource to `_RESOURCES` and a section in `cluster_problems()`.
4. Make sure the chart's RBAC can list the resource type (`charts/k8s-servant/templates/clusterrole.yaml`;
      `view` covers most namespaced types).
5. Mention it in the `cluster-health-check` skill and in [tools.md](tools.md).
6. Try it on a real cluster: run the tool directly and compare with `kubectl`.

Note: the problem lines feed the "anything changed?" comparison of scheduled checks.
A line that contains a value changing every hour (a counter, a timestamp) makes every
check run in full. Keep lines stable.

### Add a tool

1. Build a `Tool(name, description, parameters, run, surface)`
      (`tools/registry.py`). `run(arguments: dict) -> str` must never raise for expected
      failures: return `ERROR: ...` text the model can react to.
2. Write the description for the model: what it returns, when to use it, limits.
3. Register it in `runtime.open_runtime()`; registration order decides precedence on
      name clashes (MCP first).
4. A new surface needs a name in `SURFACE_NAMES` (`agent.py`).
5. Tests: arguments validated, output format, failure as `ERROR:` text, registration.
6. Document it in [tools.md](tools.md); review [security.md](security.md) if it reads
      something new.

### Add or change a skill

1. A built-in skill: `charts/k8s-servant/knowledge/skills/<name>/SKILL.md` with `name` and
      `description` frontmatter. It must stay generic.
2. A site-specific skill: `knowledge.extraSkills` in the environment values.
3. The description decides whether the model ever loads it: start with "Use when ...".
4. Check: `k8s-servant --values ... tools` lists it; `ask` a question that should
      trigger it and look for `load_skill` in the log.
      See [knowledge-and-skills.md](knowledge-and-skills.md).

### Change the system prompt

`BASE_PROMPT` in `agent.py`. Every rule there costs tokens on every call and can change
behaviour in unexpected places, so add a rule only for a failure you have seen, write
it as an instruction with the reason, and add a test that the rule is present (see
`tests/test_agent.py`). Then run `ask` and `check` against a real cluster.

### Add a CLI command

1. `cmd_<name>(args, config, knowledge, memory) -> int` in `cli.py`; register the
      sub-parser in `build_parser()`.
2. Exit codes: 0 ok, 1 runtime failure, 2 configuration error.
3. Test it through `cli.main([...])` in `tests/test_cli.py`.
4. Document it in [cli.md](cli.md).

### Add a component to the onboard catalog

`k8s-servant onboard` recognises well-known components through `CATALOG` in
`k8s_servant/scan/catalog.py`, one row per component. For each row the scan checks two
traces, using the objects it has already read:

- **Running:** a Deployment, DaemonSet or StatefulSet whose name matches the row, with
    at least one ready pod.
- **API installed:** the CRDs or webhook configurations the row declares.

Both found, or running with no API declared, gives a statement with the evidence
("Kyverno (policy engine) runs in namespace `kyverno` [CRD group kyverno.io;
Deployment kyverno/kyverno-admission-controller (1 ready)]"). One without the other
gives a question (half-installed, left over). Only the row's default namespace gives
"is it used here?". No trace gives nothing.

Rows do more than name things. A namespace where a catalog component runs counts as
platform, not application, which the network facts rely on. The scan also trusts
Prometheus and Alertmanager Services only in namespaces where their catalog
controllers run.

1. Find the traces: `helm template` the upstream chart with its default values and
      note the workload names, the CRD groups it always installs, its webhook
      configurations and its usual namespace.
2. Add the row with `_c(key, name, topic, role, ...)`:

    ```python
    _c(
        "external-dns",
        "ExternalDNS",
        "components",
        "DNS records from Services and Ingresses",
        workloads=("external-dns",),
        namespaces=("external-dns",),
    )
    ```

    - `key` is stored as `component/<key>` and the re-scan compares by it: never rename it.
    - `topic` is the draft section: `components`, `mesh`, `secrets`, `monitoring` or `network`.
    - `workloads=` and `webhooks=` are regular expressions matched against the whole
        name, in any namespace. Allow a release prefix where charts add one:
        `r"(.+-)?ingress-nginx-controller"`.
    - Declare `crds=` (API groups) or `webhooks=` only when **every** install has them;
        otherwise an install without them becomes an "installed fully?" question.
        Use `crd_names=` (exact CRD names) when other projects share the group, as
        Argo CD and Argo Rollouts share `argoproj.io`.
    - `only_ask=` is for workloads that don't prove the feature is used (Cilium Envoy
        runs without the mesh features): the draft then asks instead of stating.

3. Test the row in `tests/test_scan_catalog.py` with `make_inputs` and `deploy` from
      `tests/fakes.py`:

    ```python
    def test_external_dns_is_seen_from_its_deployment():
        facts, _ = component_facts(make_inputs(deployments=[deploy("external-dns", "external-dns")]))
        found = by_key(facts, "component/external-dns")
        assert found.certainty == "seen"
        assert found.statement == "ExternalDNS (DNS records from Services and Ingresses) runs in namespace `external-dns`"
    ```

    `test_every_catalog_row_is_valid` checks every row: a unique key, a known topic,
    at least one trace, and regular expressions that compile.

4. Run `onboard --no-input --no-model` against a cluster that has the component (a
      kind cluster is enough) and read the line in the draft.

The draft, the questions and the re-scan pick up a new row without other changes.
Components outside the catalog are not recognised yet; see
[#67](https://github.com/k8s-servant/k8s-servant/issues/67).

## Before you commit

```bash
.venv/bin/pytest            # everything green
git diff --stat             # only what you meant to change
```

Then a pull request with a [Conventional Commit](https://www.conventionalcommits.org/)
title that says *why*: release-please builds the CHANGELOG from those titles. See
[CONTRIBUTING.md](https://github.com/k8s-servant/k8s-servant/blob/main/CONTRIBUTING.md).
