# AGENTS.md

Guidance for AI coding assistants (Claude Code, Codex, Copilot, Cursor and others)
working in this repository. Human contributors: see [CONTRIBUTING.md](CONTRIBUTING.md).
`CLAUDE.md` only imports this file, so keep the guidance here.

## Project

k8s-servant is a **read-only** AI SRE agent for Kubernetes. It talks to a chat model
(Foundry, OpenAI, Anthropic, Ollama or any OpenAI-compatible server; see
`docs/model-providers.md`), reads the cluster through kubernetes-mcp-server (MCP), and
falls back to a guarded `kubectl`. It runs as a CLI locally, or in-cluster (Helm chart)
where it runs scheduled health checks and reports.

## Commands

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev,azure]'     # setup
.venv/bin/pytest                                                # tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .       # lint
set -a; source .env; set +a                                     # local settings (see .env.example)
.venv/bin/k8s-servant doctor | tools | ask "..."                # CLI
.venv/bin/k8s-servant onboard --no-input                        # draft the knowledge files from a scan
kubernetes-mcp-server --config deploy/local/mcp-config.toml     # local MCP server
helm lint charts/k8s-servant                                    # chart
helm template k8s-servant charts/k8s-servant -f examples/values-azure.yaml   # render
docker build -t k8s-servant:dev .                               # image
python -m build && twine check --strict dist/*                  # PyPI package (sdist + wheel)
pip install -e '.[docs]' && zensical build --strict             # docs site (k8s-servant.dev)
```

## Architecture in brief

- `cli.py` → `runtime.open_runtime()`, which registers MCP tools, then `kubectl`, then
  `load_skill` → `agent.Agent.run()`, the tool-calling loop → the `llm` package, with one
  client per API (`responses.py`, `messages.py`, `chat_completions.py`) and sign-in in
  `auth.py`.
- Tools share one shape (`tools/registry.Tool`). `ToolRegistry.run()` never raises; it
  returns `ERROR: ...` text.
- Knowledge (environment description, cluster notes, `SKILL.md` skills) comes from
  `KNOWLEDGE_DIR` and/or Helm values files (`--values`).

## Conventions (must follow)

- **Read-only, always.** No code path may change cluster objects. Never read Secrets:
  the kubectl guard refuses them, and the MCP config denies them.
- **The kubectl guard is an allow-list.** Verbs, flags and `-o` formats are allow-listed
  in `tools/kubectl_tool.py`. Never switch to a deny-list. Add a flag only if it cannot
  change the identity or target server, read or write local files, or block.
- **MCP toolsets:** only `core`. Never enable `config` (leaks kubeconfig credentials) or
  `helm` (reads Secrets).
- **No shell.** Use `asyncio.create_subprocess_exec` with an argument list.
- **stdout carries only the answer.** Logs go to stderr.
- **Settings come only from environment variables**, via `config.configure_app()`, with
  a comment per key and a row in `docs/configuration.md` (a test checks both ways).
- **No `temperature`** on model calls (reasoning models reject it).
- **Comments:** every module starts with a docstring explaining what and why. Google-style
  docstrings. Comments explain intent, not mechanics.
- **Built-in skills stay generic.** No site names, no write commands
  (`tests/test_builtin_skills.py` enforces this). Site facts go in the environment
  description.
- **TDD:** write a failing test first; one test file per module. Test doubles live in
  `tests/fakes.py`. `cli.main()` reconfigures logging to stderr, so CLI tests read
  stderr, not `caplog`.
- **A failed or unparseable health check never changes the findings in memory.**
- **Webhook URLs and API keys are credentials.** Never log them, not even in error messages.
- **Times are UTC** everywhere, except the morning report's schedule and title
  (`MORNING_REPORT_TIMEZONE`).
- **Chart rules:** a new per-cluster setting goes into `appConfig`; RBAC stays
  get/list/watch, never `secrets`, `*` or `nodes/proxy`. Versions are managed by
  release-please; do not bump them by hand.
- **Chart credentials:** the chart never creates Secrets and never depends on a secret
  tool (ESO, Sealed Secrets, ...). A credential is an `existingSecret` value, mounted as
  a file and read through a `*_FILE` setting, never an environment variable. Anything
  site-specific goes through the extension points (`extraEnv`, `extraEnvFrom`,
  `extraVolumes`, `extraVolumeMounts`, `extraObjects`), not new tool-specific values.
- **Provider-neutral docs.** Write "the model" or "the model provider". Name a provider
  (Microsoft Foundry, OpenAI, Anthropic, Ollama, ...) only in provider-specific sections,
  recipes, examples and lists of all providers.
- **Public repository.** Never add real cluster names, registries, tenant or
  subscription IDs, hostnames, company names or personal data to code, tests, docs or
  examples. Use placeholders (`example.com`, `00000000-0000-0000-0000-000000000000`,
  `my-cluster`).
- **Git:** Conventional Commit messages and PR titles (`feat:`, `fix(chart):`, ...).
  The changelog is generated from them.
- **Packaging:** the PyPI package lists its subpackages by hand in `pyproject.toml`,
  because the built-in skills are mapped in from the chart folder as
  `k8s_servant/_knowledge`. Add a new subpackage there (`tests/test_packaging.py`
  checks). The PyPI page is `README.pypi.md`: absolute links only.
- **Changelog:** never create or edit `CHANGELOG.md`. release-please writes it, and only
  produces a clean file when it starts absent or empty.
- **Release signing identity:** releases are signed keyless as
  `.github/workflows/release-please.yml@refs/heads/main`, and users pin that string in
  their policies (`docs/verifying-releases.md`). Don't rename that file or release from
  another branch. Keep `id-token: write` limited to the publish jobs, since any job in
  that file can sign as the identity. Keep signing the multi-arch index digest, so the
  SBOM and provenance stay covered.

## Review feedback (human or bot)

- A review is input to check, not a list of orders. Verify each point against the code
  or the upstream docs before changing anything. Fix what is right, and reply with the
  evidence when a suggestion is wrong.
- The Claude review workflow posts its summary as a PR conversation comment and its
  line findings as inline comments. Read both (`gh pr view N --comments` and
  `gh api repos/{owner}/{repo}/pulls/N/comments`) before saying a PR has no feedback.
