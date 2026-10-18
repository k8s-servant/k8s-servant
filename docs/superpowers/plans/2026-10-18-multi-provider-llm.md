# Multi-provider model support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let k8s-servant use Foundry, OpenAI, Anthropic, Ollama or any OpenAI-compatible endpoint, each through the API that supports reasoning plus tools, with `LLM_*` settings and `LLM_AUTH`.

**Architecture:** The agent talks to a neutral `ChatModel` interface with neutral `Turn`, `ToolCall` and `ToolSpec` types. Three API clients implement it: Responses (`foundry`, `openai`), Messages (`anthropic`, native SDK) and Chat Completions (`ollama`, `openai-compatible`). A provider table picks the client; `resolve_model_auth` picks API key, Entra ID or none.

**Tech Stack:** Python 3.12+, `openai` 3.x (Responses and Chat Completions), `anthropic` 1.x (`AsyncAnthropic`, `AsyncAnthropicFoundry`), optional `azure-identity`, pytest + pytest-asyncio, Helm.

**Spec:** `docs/superpowers/specs/2026-10-18-multi-provider-llm-design.md` (read it before any task).

## Global Constraints

- Settings only from environment variables via `config.configure_app()`, one comment per key.
- No `temperature` on any model call.
- An API key never appears in logs, error messages or `doctor` output.
- Responses API calls always send `store=False`.
- stdout carries only the answer; logs go to stderr.
- Old names `FOUNDRY_ENDPOINT`, `FOUNDRY_DEPLOYMENT`, `FOUNDRY_API_KEY`, `FOUNDRY_API_KEY_FILE`, `FOUNDRY_TOKEN_SCOPE`, `FOUNDRY_TIMEOUT_SECONDS`, `FOUNDRY_REASONING_EFFORT` are not read; if set, startup fails naming the new name.
- Foundry endpoint = host ending in `.openai.azure.com`, `.services.ai.azure.com` or `.cognitiveservices.azure.com`.
- Never edit `CHANGELOG.md` and never bump versions by hand. Conventional Commit messages; the PR title is `feat!: support multiple model providers (#2)`.
- TDD: failing test first; one test file per module; shared doubles in `tests/fakes.py`.
- Every module starts with a docstring (what and why); Google-style docstrings.
- Chart: never creates Secrets; a credential is `existingSecret` or `file`, mounted and read through `*_FILE`.
- Public repo: placeholders only (`example.com`, `my-foundry`, `00000000-0000-0000-0000-000000000000`).
- Run `.venv/bin/pytest`, `.venv/bin/ruff check . && .venv/bin/ruff format --check .` and `helm lint charts/k8s-servant` before every commit.

## Review Focus

1. **Responses function tools default to strict schemas.** `FunctionToolParam.strict` is required in the SDK; our MCP tool schemas are not strict-compatible. Expect every tool sent with `strict: False` (test in Task 4).
2. **Parallel tool calls on the Messages API.** Several `tool` turns after one assistant turn must become one `user` message with several `tool_result` blocks; separate messages are rejected (test in Task 5).
3. **Keys inside SDK error text.** OpenAI's 401 body echoes part of the key ("Incorrect API key provided: sk-..."). Expect `model_error_text` output to never contain the configured key, for every status (test in Task 3).
4. **Assistant turn with tool calls and no text.** The Messages API rejects empty text blocks; Responses rejects empty message items. Expect no empty text sent (tests in Tasks 4 and 5).
5. **Follow-up after a tool-using run.** The health check's JSON retry and the iteration-limit call send a history with tool calls but must not call tools. Expect Messages and Responses to send the tool definitions with tool choice "none", and Chat Completions to omit tools as today (tests in Tasks 1, 4, 5).

---

## File Structure

| File | Responsibility |
|---|---|
| `k8s_servant/llm/__init__.py` | Public surface: `build_chat`, `describe_model`, `model_error_text`, `explain_model_error`, `MODEL_ERRORS`, re-exported types |
| `k8s_servant/llm/types.py` | `Turn`, `ToolCall`, `ToolSpec`, `ChatModel`, `ModelOutputTruncated`, `log_usage` |
| `k8s_servant/llm/providers.py` | `Provider`, `PROVIDERS`, `is_foundry_endpoint`, `normalize_endpoint` |
| `k8s_servant/llm/auth.py` | `ModelAuth`, `resolve_model_auth`, `entra_token_provider` |
| `k8s_servant/llm/errors.py` | Per-provider hints, `explain_model_error`, `model_error_text`, `MODEL_ERRORS` |
| `k8s_servant/llm/chat_completions.py` | Chat Completions client (today's `FoundryChat`, moved) |
| `k8s_servant/llm/responses.py` | Responses client |
| `k8s_servant/llm/messages.py` | Messages client |
| `k8s_servant/llm.py` | Deleted (replaced by the package) |
| Tests | `tests/test_llm_types.py`, `test_llm_providers.py`, `test_llm_auth.py`, `test_llm_errors.py`, `test_llm_chat_completions.py`, `test_llm_responses.py`, `test_llm_messages.py`, `test_llm_factory.py`; `tests/test_llm.py` is split into these and removed |

---

### Task 1: Neutral types and the agent on top of them

Pure refactor: behaviour and settings unchanged (still `FOUNDRY_*`), but nothing outside `k8s_servant/llm/` imports from `openai`.

**Files:**
- Create: `k8s_servant/llm/__init__.py`, `k8s_servant/llm/types.py`, `k8s_servant/llm/chat_completions.py`
- Delete: `k8s_servant/llm.py` (its content moves to `chat_completions.py` and `__init__.py`)
- Modify: `k8s_servant/agent.py`, `k8s_servant/report.py:323`, `k8s_servant/cli.py:439`, `k8s_servant/runtime.py`, `k8s_servant/tools/registry.py:84-92`, `tests/fakes.py`, every test using `fakes.answer` / `fakes.tool_call` / `schemas()`
- Test: `tests/test_llm_types.py`, `tests/test_llm_chat_completions.py` (move the `FoundryChat` tests from `tests/test_llm.py`), `tests/test_agent.py`

**Interfaces (Produces):**
```python
# k8s_servant/llm/types.py
@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text, exactly as the model produced it


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict  # JSON schema


@dataclass
class Turn:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str = ""  # set on role="tool"
    provider_state: Any = None  # opaque; only the API client that produced it reads it


class ModelOutputTruncated(Exception):
    """The reply was cut off by a token limit; never treated as an answer."""


class ChatModel(Protocol):
    total_tokens: int

    async def complete(self, turns: list[Turn], tools: list[ToolSpec], *, use_tools: bool = True) -> Turn: ...


def log_usage(prompt: int, cached: int, completion: int, reasoning: int, seconds: float) -> None:
    """One INFO line per model call, same wording as today's FoundryChat line."""
```
- `ToolRegistry.specs() -> list[ToolSpec]` replaces `schemas()`.
- `ChatCompletionsChat(client: AsyncOpenAI, model: str, reasoning_effort: str)` in `chat_completions.py` (renamed `FoundryChat`).
- `Agent.run` / `follow_up` keep signatures; `AgentResult.messages: list[Turn]`; `follow_up(messages: list[Turn], text: str) -> str`.
- `tests/fakes.py`: `answer(text) -> Turn`, `tool_call(name, args, call_id="c1") -> Turn`; `ScriptedChat.complete(turns, tools, *, use_tools=True)` records `{"turns": [...], "tools": tools, "use_tools": use_tools}`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_agent.py (adapt existing tests to Turn, add:)
async def test_limit_call_passes_tools_but_forbids_using_them():
    chat = ScriptedChat(*[tool_call("t", {})] * 2, answer("done"))
    result = await make_agent(chat, max_iterations=2).run("q")
    last = chat.calls[-1]
    assert last["use_tools"] is False and last["tools"]  # definitions still sent
    assert result.hit_limit


async def test_follow_up_sends_tools_with_use_tools_false():
    ...
    assert chat.calls[-1]["use_tools"] is False and chat.calls[-1]["tools"]


# tests/test_llm_chat_completions.py
async def test_use_tools_false_omits_tools():  # today's behaviour on this API
    ...
    assert "tools" not in client.last_kwargs


async def test_turns_convert_to_chat_messages():
    turns = [
        Turn("system", "S"),
        Turn("user", "Q"),
        Turn("assistant", "", [ToolCall("c1", "t", "{}")]),
        Turn("tool", "out", tool_call_id="c1"),
    ]
    ...
    assert client.last_kwargs["messages"] == [
        {"role": "system", "content": "S"},
        {"role": "user", "content": "Q"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "t", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "out"},
    ]


async def test_length_finish_reason_raises_truncated():
    ...
    with pytest.raises(ModelOutputTruncated):
        await chat.complete([Turn("user", "q")], [])
```

- [ ] **Step 2: Run them and see them fail**

Run: `.venv/bin/pytest tests/test_agent.py tests/test_llm_chat_completions.py -q`
Expected: FAIL (ImportError on `k8s_servant.llm.types`).

- [ ] **Step 3: Implement**

Create the package; move `normalize_endpoint`, `build_client`, `build_chat`, `explain_model_error`, `_STATUS_HINTS` into it unchanged except: the client converts `Turn`s to Chat Completions messages and the reply to a `Turn`; `finish_reason == "length"` raises `ModelOutputTruncated`. `agent.py` drops the `openai` import and its own `ChatModel`/`_assistant_message`; it appends the returned `Turn` as is, tool results as `Turn("tool", output, tool_call_id=call.id)`, and passes `self.registry.specs()` with `use_tools=False` on the limit call and in `follow_up`. `report.py` overview call: `[Turn("system", OVERVIEW_PROMPT), Turn("user", ...)]`, `[]`. `cli.py` doctor: `[Turn("user", "Reply with the single word OK.")]`.

- [ ] **Step 4: Run the whole suite and lint**

Run: `.venv/bin/pytest -q && .venv/bin/ruff check . && .venv/bin/ruff format --check .`
Expected: all pass; `grep -rn "from openai" k8s_servant | grep -v k8s_servant/llm/` prints nothing.

- [ ] **Step 5: Commit**

```bash
git add -A k8s_servant tests
git commit -m "refactor: give the agent neutral model types (#2)"
```

---

### Task 2: `LLM_*` settings and the provider table

**Files:**
- Create: `k8s_servant/llm/providers.py`
- Modify: `k8s_servant/config.py:149-168, 251-258`, `k8s_servant/llm/chat_completions.py` and `__init__.py` (read the new keys), `k8s_servant/cli.py` (messages pointing at `docs/model-providers.md`), `.env.example`, README configuration table (`tests/test_docs.py` enforces it), `tests/test_docs.py:38` (`LLM_ENDPOINT` instead of `FOUNDRY_ENDPOINT`), `tests/test_cli.py` (`ENV_VARS`)
- Test: `tests/test_llm_providers.py`, `tests/test_config.py`

**Interfaces (Produces):**
```python
# k8s_servant/llm/providers.py  (imports nothing from the package)
@dataclass(frozen=True)
class Provider:
    name: str
    api: Literal["responses", "messages", "chat_completions"]
    api_label: str  # "Responses API" | "Messages API" | "Chat Completions API"
    default_endpoint: str  # "" = LLM_ENDPOINT required
    default_effort: str


PROVIDERS: dict[str, Provider] = {
    "foundry": Provider("foundry", "responses", "Responses API", "", "medium"),
    "openai": Provider("openai", "responses", "Responses API", "https://api.openai.com/v1", "medium"),
    "anthropic": Provider("anthropic", "messages", "Messages API", "https://api.anthropic.com", "medium"),
    "ollama": Provider("ollama", "chat_completions", "Chat Completions API", "http://localhost:11434/v1", ""),
    "openai-compatible": Provider("openai-compatible", "chat_completions", "Chat Completions API", "", ""),
}
OLD_SETTINGS: dict[str, str]  # FOUNDRY_ENDPOINT -> LLM_ENDPOINT, FOUNDRY_DEPLOYMENT -> LLM_MODEL, ... (all seven)


def is_foundry_endpoint(endpoint: str) -> bool: ...
def normalize_endpoint(provider: str, endpoint: str) -> str:
    """Base URL with trailing slash. foundry: bare host gets /openai/v1.
    anthropic with a Foundry endpoint: bare host gets /anthropic.
    https only; http allowed for localhost/127.0.0.1/::1 always, and for ollama and
    openai-compatible also for single-label hosts and hosts ending in .svc or
    .svc.cluster.local. ValueError names LLM_ENDPOINT."""
```
Config keys (replace all `foundry_*`): `llm_provider`, `llm_endpoint` (provider default applied when empty), `llm_model`, `llm_api_key` (`_secret(env, "LLM_API_KEY")`), `llm_api_key_source` (`"file <path>"`, `"LLM_API_KEY"` or `""`), `llm_auth` (`_choice(..., "auto", ("auto", "entra", "api-key"))`), `llm_token_scope` (default `https://ai.azure.com/.default`), `llm_reasoning_effort` (`_choice` over `("", "none", "minimal", "low", "medium", "high", "xhigh", "max")`, default `PROVIDERS[provider].default_effort`; when `LLM_PROVIDER` is unset or unknown the default is `""`), `llm_timeout_seconds` (120), `llm_max_output_tokens` (32000), `workload_identity` (`bool(env.get("AZURE_FEDERATED_TOKEN_FILE", "").strip())`).
- `LLM_PROVIDER` unknown value → `ValueError` listing the valid names; empty is allowed here and reported by `missing_model_settings`.
- `missing_model_settings(config) -> list[str]`: `LLM_PROVIDER`, `LLM_MODEL`, and `LLM_ENDPOINT` when the provider's `default_endpoint` is empty.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_config.py
def test_old_foundry_setting_fails_with_the_new_name():
    with pytest.raises(ValueError, match="FOUNDRY_DEPLOYMENT.*LLM_MODEL"):
        configure_app({"FOUNDRY_DEPLOYMENT": "x"})


@pytest.mark.parametrize(
    "provider,effort",
    [("foundry", "medium"), ("openai", "medium"), ("anthropic", "medium"), ("ollama", ""), ("openai-compatible", "")],
)
def test_reasoning_effort_default_depends_on_provider(provider, effort):
    assert configure_app({"LLM_PROVIDER": provider})["llm_reasoning_effort"] == effort


def test_unknown_provider_is_rejected():
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        configure_app({"LLM_PROVIDER": "bedrock"})


def test_provider_default_endpoint_is_applied():
    assert configure_app({"LLM_PROVIDER": "ollama"})["llm_endpoint"] == "http://localhost:11434/v1"


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


def test_key_source_names_the_file(tmp_path): ...  # == f"file {path}"
def test_workload_identity_detected(): ...


# tests/test_llm_providers.py
@pytest.mark.parametrize(
    "host,expected",
    [
        ("my-foundry.openai.azure.com", True),
        ("my-foundry.services.ai.azure.com", True),
        ("my-foundry.cognitiveservices.azure.com", True),
        ("openai.azure.com.example.com", False),
        ("api.anthropic.com", False),
    ],
)
def test_is_foundry_endpoint(host, expected):
    assert is_foundry_endpoint(f"https://{host}/") is expected


@pytest.mark.parametrize(
    "provider,url,expected",
    [
        ("foundry", "https://my-foundry.openai.azure.com", "https://my-foundry.openai.azure.com/openai/v1/"),
        (
            "anthropic",
            "https://my-foundry.services.ai.azure.com",
            "https://my-foundry.services.ai.azure.com/anthropic/",
        ),
        ("anthropic", "https://api.anthropic.com", "https://api.anthropic.com/"),
        ("ollama", "http://ollama.ollama.svc:11434/v1", "http://ollama.ollama.svc:11434/v1/"),
        ("openai-compatible", "http://vllm:8000/v1", "http://vllm:8000/v1/"),
        ("openai-compatible", "http://vllm.ai.svc.cluster.local:8000/v1", "http://vllm.ai.svc.cluster.local:8000/v1/"),
    ],
)
def test_normalize_endpoint(provider, url, expected): ...


@pytest.mark.parametrize(
    "provider,url",
    [
        ("foundry", "http://vllm:8000/v1"),
        ("openai-compatible", "http://models.example.com/v1"),
        ("anthropic", "http://localhost.example.com"),
    ],
)
def test_normalize_endpoint_rejects_remote_http(provider, url):
    with pytest.raises(ValueError, match="LLM_ENDPOINT"):
        normalize_endpoint(provider, url)
```

- [ ] **Step 2: Run and see them fail**

Run: `.venv/bin/pytest tests/test_config.py tests/test_llm_providers.py -q`
Expected: FAIL.

- [ ] **Step 3: Implement** the table, `normalize_endpoint` (moved from `__init__.py` and extended), the config keys above with comments, the old-name check at the top of `configure_app`, and switch the Chat Completions client and `build_chat` to the new keys. Update `.env.example` and the README table to the new names.

- [ ] **Step 4: Run the whole suite and lint** (as in Task 1). Expected: all pass; `grep -rn "foundry_\|FOUNDRY_" k8s_servant tests` shows only `OLD_SETTINGS` and its test.

- [ ] **Step 5: Commit**: `git commit -m "feat!: rename the model settings to LLM_* and add LLM_PROVIDER (#2)"`

---

### Task 3: Sign-in, the factory and error messages

**Files:**
- Create: `k8s_servant/llm/auth.py`, `k8s_servant/llm/errors.py`
- Modify: `k8s_servant/llm/__init__.py`, `k8s_servant/cli.py` (drop the `azure.core` import; use `MODEL_ERRORS` and `model_error_text` from `k8s_servant.llm`), `k8s_servant/report.py:259`, `pyproject.toml` (move `azure-identity` to `[project.optional-dependencies] azure`; add `"anthropic>=1.15,<2"` to dependencies with a comment), `Dockerfile:41` (`pip install ... '.[azure]'`)
- Test: `tests/test_llm_auth.py`, `tests/test_llm_errors.py`, `tests/test_llm_factory.py`

**Interfaces:**
- Consumes: `PROVIDERS`, `is_foundry_endpoint` (Task 2); `ChatCompletionsChat` (Task 1).
- Produces:
```python
# auth.py
@dataclass(frozen=True)
class ModelAuth:
    method: Literal["api-key", "entra", "none"]
    source: str  # "file /var/run/secrets/llm/api-key" | "LLM_API_KEY" | "Workload Identity" | "az login / DefaultAzureCredential" | ""
    label: str  # for doctor: "API key (file ...)", "Entra ID (Workload Identity)", "no key"


def resolve_model_auth(config: dict) -> ModelAuth:
    """Apply LLM_AUTH rules from spec §2. ValueError naming the setting on an invalid
    combination. Logs one WARNING under auto when workload_identity and a key are both
    present (never the key)."""


def entra_token_provider(scope: str) -> Callable[[], Awaitable[str]]:
    """Async cached token provider (azure-identity in a worker thread, as today).
    ValueError "install k8s-servant[azure]" when azure-identity is missing."""


# errors.py
MODEL_ERRORS: tuple[
    type[BaseException], ...
]  # openai.OpenAIError, anthropic.AnthropicError, ModelOutputTruncated, and azure ClientAuthenticationError when importable


def explain_model_error(exc: BaseException, provider: str = "") -> str: ...
def model_error_text(exc: BaseException, config: dict) -> str:
    """explain_model_error with the provider from config, with config["llm_api_key"] redacted."""


# __init__.py
def build_chat(
    config: dict,
) -> ChatModel: ...  # resolve auth, pick client by PROVIDERS[...].api, log describe_model once
def describe_model(config: dict) -> str:
    """e.g. "foundry / my-deployment via Responses API, Entra ID (Workload Identity)"."""
```
Auth rules (spec §2), `eligible = provider == "foundry" or (provider in ("anthropic", "openai-compatible") and is_foundry_endpoint(endpoint))`:
- `auto`: key → api-key (warn if `workload_identity` and `eligible`); else `eligible` → entra; else provider in (`ollama`, `openai-compatible`) → none; else ValueError "set LLM_API_KEY_FILE".
- `entra`: not `eligible` → ValueError; key set → ValueError.
- `api-key`: no key → ValueError.

Hints: Foundry keeps today's 401/403/404/429 texts with `LLM_*` names; other providers: 401 "the API key was rejected (check LLM_API_KEY_FILE)", 404 "LLM_MODEL is not a model this endpoint serves, or LLM_ENDPOINT is wrong", 429 "rate limited", connection "cannot reach LLM_ENDPOINT", timeout "raise LLM_TIMEOUT_SECONDS", `ModelOutputTruncated` "the reply hit the output limit: raise LLM_MAX_OUTPUT_TOKENS or lower LLM_REASONING_EFFORT". Unlisted API status: `f"HTTP {status}: {message}"` (redacted by `model_error_text`).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm_auth.py
@pytest.mark.parametrize(
    "provider,endpoint",
    [
        ("anthropic", "https://my-foundry.services.ai.azure.com"),
        ("openai-compatible", "https://my-foundry.openai.azure.com/openai/v1"),
        ("foundry", "https://my-foundry.openai.azure.com"),
    ],
)
def test_auto_without_key_on_foundry_endpoints_is_entra(provider, endpoint): ...  # method == "entra"


def test_entra_rejected_for_anthropic_direct():
    with pytest.raises(ValueError, match="LLM_AUTH=entra"):
        ...


def test_entra_rejected_when_a_key_is_set(): ...
def test_api_key_mode_without_key_fails(): ...
def test_openai_without_key_fails_under_auto(): ...
def test_ollama_and_openai_compatible_run_without_key(): ...  # method == "none"
def test_auto_warns_when_workload_identity_and_key(caplog):
    ...
    assert "Workload Identity" in caplog.text and "sk-test-secret" not in caplog.text


def test_label_names_the_key_file_not_the_key(tmp_path): ...


# tests/test_llm_errors.py
@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500])
def test_model_error_text_never_contains_the_key(status):
    exc = make_status_error(status, body="Incorrect API key provided: sk-test-secret")
    assert "sk-test-secret" not in model_error_text(exc, {"llm_provider": "openai", "llm_api_key": "sk-test-secret"})


def test_foundry_401_hint_mentions_the_role(): ...
def test_truncated_hint_names_the_settings(): ...


# tests/test_llm_factory.py
@pytest.mark.parametrize(
    "provider,cls",
    [
        ("foundry", ResponsesChat),
        ("openai", ResponsesChat),
        ("anthropic", MessagesChat),
        ("ollama", ChatCompletionsChat),
        ("openai-compatible", ChatCompletionsChat),
    ],
)
def test_build_chat_picks_the_client(provider, cls): ...
def test_describe_model_examples(): ...  # the three strings in spec §6.4
def test_entra_without_azure_identity_says_install_the_extra(monkeypatch): ...
```
In this task the parametrization lists only `ollama` and `openai-compatible`; Tasks 4 and 5 add their providers' cases. Until then `build_chat` raises `NotImplementedError` for `foundry`, `openai` and `anthropic` (removed in Tasks 4 and 5, so no commit after Task 5 contains it).

- [ ] **Step 2: Run and see them fail.** `.venv/bin/pytest tests/test_llm_auth.py tests/test_llm_errors.py tests/test_llm_factory.py -q` → FAIL.

- [ ] **Step 3: Implement** the modules above; the Chat Completions client uses `entra_token_provider` for entra, the key for api-key, and the placeholder key `"unused"` for none (the SDK requires a value). Switch every `explain_model_error(exc)` call site in `cli.py` and `report.py` to `model_error_text(exc, config)`.

- [ ] **Step 4: Run the whole suite and lint.** Also: `.venv/bin/pip install -e '.[dev]'` in a fresh venv without the extra and run `tests/test_llm_factory.py` (the install hint test passes, nothing else imports azure at module level).

- [ ] **Step 5: Commit**: `git commit -m "feat: add LLM_AUTH, the client factory and per-provider error hints (#2)"`

---

### Task 4: Responses API client

**Files:**
- Create: `k8s_servant/llm/responses.py`
- Modify: `k8s_servant/llm/__init__.py` (factory), `tests/test_llm_factory.py` (add the two `ResponsesChat` cases)
- Test: `tests/test_llm_responses.py`

**Interfaces:**
- Consumes: `Turn`, `ToolCall`, `ToolSpec`, `ModelOutputTruncated`, `log_usage` (Task 1); `ModelAuth`, `entra_token_provider` (Task 3).
- Produces: `ResponsesChat(client: AsyncOpenAI, model: str, reasoning_effort: str)` implementing `ChatModel`.

Request shape: `model`, `input` (items), `store=False`, `include=["reasoning.encrypted_content"]`, `reasoning={"effort": e}` only when `e` is non-empty, `tools=[{"type": "function", "name", "description", "parameters", "strict": False}]` when tools given, `tool_choice="none"` when `use_tools` is False and tools given. Conversion: system and user turns → `{"role": ..., "content": text}`; an assistant turn with `provider_state` → its items re-sent as stored; without → a message item (only if text is non-empty) plus `{"type": "function_call", "call_id", "name", "arguments"}` items; tool turns → `{"type": "function_call_output", "call_id", "output"}`. Reply: `ToolCall(call_id, name, arguments)` per `function_call` output item, `content=response.output_text`, `provider_state` = the output items as dicts accepted back as input (verify against the SDK's input item types). `status == "incomplete"` → `ModelOutputTruncated`. Usage: `input_tokens`, `input_tokens_details.cached_tokens`, `output_tokens`, `output_tokens_details.reasoning_tokens`.

- [ ] **Step 1: Write failing tests** (fake `client.responses.create` recording kwargs, returning SDK `Response` objects built with `Response.model_validate`)

```python
async def test_request_is_stateless_and_carries_encrypted_reasoning():
    ...
    assert kw["store"] is False and kw["include"] == ["reasoning.encrypted_content"]
    assert "temperature" not in kw


async def test_tools_are_sent_non_strict():
    ...
    assert kw["tools"] == [
        {"type": "function", "name": "t", "description": "d", "parameters": {"type": "object"}, "strict": False}
    ]


async def test_reasoning_items_are_sent_back_on_the_next_call():
    first = await chat.complete([Turn("user", "q")], specs)  # reply: reasoning item + function_call
    await chat.complete([Turn("user", "q"), first, Turn("tool", "out", tool_call_id="call_1")], specs)
    second_input = client.calls[1]["input"]
    assert any(i.get("type") == "reasoning" and i.get("encrypted_content") == "ENC" for i in second_input)
    assert second_input[-1] == {"type": "function_call_output", "call_id": "call_1", "output": "out"}


async def test_use_tools_false_sends_tool_choice_none(): ...
async def test_assistant_turn_without_text_sends_no_empty_message(): ...
async def test_incomplete_raises_truncated(): ...
async def test_effort_omitted_when_empty(): ...
async def test_usage_is_logged_and_counted(caplog): ...
```

- [ ] **Step 2: Run and see them fail.** `.venv/bin/pytest tests/test_llm_responses.py -q` → FAIL.
- [ ] **Step 3: Implement** `ResponsesChat` and wire `foundry`/`openai` to it in `build_chat` (client: `AsyncOpenAI(base_url=normalize_endpoint(...), api_key=<key | entra provider>, timeout=..., max_retries=3)`).
- [ ] **Step 4: Run the whole suite and lint.**
- [ ] **Step 5: Commit**: `git commit -m "feat: use the Responses API for Foundry and OpenAI (#2)"`

---

### Task 5: Messages API client

**Files:**
- Create: `k8s_servant/llm/messages.py`
- Modify: `k8s_servant/llm/__init__.py` (factory), `tests/test_llm_factory.py`
- Test: `tests/test_llm_messages.py`

**Interfaces:**
- Consumes: as Task 4.
- Produces: `MessagesChat(client: AsyncAnthropic | AsyncAnthropicFoundry, model: str, reasoning_effort: str, max_output_tokens: int)`.

Client choice: `is_foundry_endpoint(endpoint)` → `AsyncAnthropicFoundry(base_url=normalize_endpoint("anthropic", endpoint), api_key=key)` or `(..., azure_ad_token_provider=entra_token_provider(scope))`; else `AsyncAnthropic(base_url=..., api_key=key)`. `timeout`, `max_retries=3` on both.

Request shape: `model`, `max_tokens=max_output_tokens`, `system` = all system turns' text joined with `"\n\n"`, `messages`, `cache_control={"type": "ephemeral"}`, tools `[{"name", "description", "input_schema"}]` when given, `tool_choice={"type": "auto"}` or `{"type": "none"}` when `use_tools` is False. Effort mapping: `""` → send neither `thinking` nor `output_config`; `"none"` → `thinking={"type": "disabled"}`; `"minimal"`/`"low"` → `"low"`; `medium`/`high`/`xhigh`/`max` as is; when sent: `thinking={"type": "adaptive"}`, `output_config={"effort": X}`. Conversion: assistant turn with `provider_state` → its content blocks as stored (thinking blocks with signatures included); without → a text block only if non-empty, plus `tool_use` blocks (`input=json.loads(arguments)`); consecutive tool turns → one `user` message of `tool_result` blocks (`tool_use_id`, `content`). Reply: text = joined text blocks, `ToolCall(id, name, json.dumps(input))` per `tool_use` block, `provider_state` = all content blocks as dicts. `stop_reason == "max_tokens"` → `ModelOutputTruncated`. Usage: prompt = `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`, cached = `cache_read_input_tokens`, completion = `output_tokens`, reasoning = 0.

- [ ] **Step 1: Write failing tests** (fake `client.messages.create`, replies built with `anthropic.types.Message.model_validate`)

```python
async def test_parallel_tool_results_become_one_user_message():
    turns = [
        Turn("user", "q"),
        Turn("assistant", "", [ToolCall("a", "t", "{}"), ToolCall("b", "t", "{}")]),
        Turn("tool", "A", tool_call_id="a"),
        Turn("tool", "B", tool_call_id="b"),
    ]
    ...
    assert kw["messages"][-1] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "a", "content": "A"},
            {"type": "tool_result", "tool_use_id": "b", "content": "B"},
        ],
    }


async def test_thinking_blocks_are_sent_back_with_signatures(): ...
async def test_system_is_top_level_and_caching_is_on():
    ...
    assert kw["system"] == "S" and kw["cache_control"] == {"type": "ephemeral"}
    assert all(m["role"] != "system" for m in kw["messages"]) and "temperature" not in kw


@pytest.mark.parametrize(
    "effort,thinking,output_config",
    [
        ("", None, None),
        ("none", {"type": "disabled"}, None),
        ("minimal", {"type": "adaptive"}, {"effort": "low"}),
        ("medium", {"type": "adaptive"}, {"effort": "medium"}),
        ("max", {"type": "adaptive"}, {"effort": "max"}),
    ],
)
async def test_effort_mapping(effort, thinking, output_config): ...


async def test_use_tools_false_keeps_tools_with_choice_none(): ...
async def test_no_empty_text_block_for_tool_only_turn(): ...
async def test_max_tokens_stop_raises_truncated(): ...
async def test_usage_counts_cache_reads(caplog): ...
def test_foundry_endpoint_uses_the_foundry_client_with_entra(): ...  # isinstance AsyncAnthropicFoundry
```

- [ ] **Step 2: Run and see them fail.** `.venv/bin/pytest tests/test_llm_messages.py -q` → FAIL.
- [ ] **Step 3: Implement** `MessagesChat` and wire `anthropic` in `build_chat`.
- [ ] **Step 4: Run the whole suite and lint.**
- [ ] **Step 5: Commit**: `git commit -m "feat: talk to Claude through the native Messages API (#2)"`

---

### Task 6: `doctor`

**Files:**
- Modify: `k8s_servant/cli.py:354-358` (config row), `:434-444` (model row)
- Test: `tests/test_cli.py`

**Interfaces:** Consumes `missing_model_settings` (Task 2), `resolve_model_auth`, `describe_model`, `model_error_text` (Task 3).

Config row: missing → `FAIL config missing LLM_... (see docs/model-providers.md)`; `resolve_model_auth` raises → `FAIL config <message>`; else `OK config <describe_model(config)>`, followed by `WARN auth ...` when `llm_auth == "auto"`, `workload_identity` and a key are all present. Model row unchanged in shape; skipped when the config row failed.

- [ ] **Step 1: Write failing tests**

```python
def test_doctor_names_provider_api_and_key_file(tmp_path, capsys, ...):
    ...  # LLM_PROVIDER=anthropic, LLM_MODEL=claude-sonnet-5-5, LLM_API_KEY_FILE=<tmp file with sk-test-secret>
    out = capsys.readouterr().out
    assert "anthropic / claude-sonnet-5-5 via Messages API, API key (file " in out
    assert "sk-test-secret" not in out

def test_doctor_fails_config_on_invalid_auth_combo(...): ...  # LLM_AUTH=entra, provider openai → FAIL config, model SKIP
def test_doctor_warns_when_workload_identity_and_key(...): ...
```

- [ ] **Step 2–4:** run (fail), implement, run the whole suite and lint.
- [ ] **Step 5: Commit**: `git commit -m "feat(cli): doctor reports the provider, API and sign-in (#2)"`

---

### Task 7: Chart and examples

**Files:**
- Modify: `charts/k8s-servant/templates/_helpers.tpl:51-54, 85, 96-97`, `charts/k8s-servant/values.yaml:90-120`, `examples/values-azure.yaml`, `examples/values-azure-csi.yaml`, `examples/values-minimal.yaml`
- Create: `examples/values-anthropic.yaml`, `examples/values-openai.yaml`, `examples/values-ollama.yaml`, `examples/values-azure-claude.yaml`
- Test: `tests/test_helm_chart.py`, `tests/test_examples.py`

Chart rules: `llmApiKey.existingSecret` / `.file` set `LLM_API_KEY_FILE` (same paths as today); required `appConfig` keys `CLUSTER_NAME`, `LLM_PROVIDER`, `LLM_MODEL`, plus `LLM_ENDPOINT` when `LLM_PROVIDER` is `foundry` or `openai-compatible`; `appConfig.LLM_API_KEY` rejected with today's message (new name); any `FOUNDRY_*` key in `appConfig` fails naming its `LLM_*` replacement. `values.yaml`: `LLM_PROVIDER: foundry`, `LLM_ENDPOINT: ""`, `LLM_MODEL: ""`, `LLM_AUTH: auto`, comments pointing at `docs/model-providers.md`.

Examples: `values-azure.yaml`, `values-azure-csi.yaml` → `LLM_PROVIDER: foundry`, `LLM_AUTH: entra`, `LLM_MODEL: my-deployment`. `values-azure-claude.yaml` → WI as in `values-azure.yaml`, `LLM_PROVIDER: anthropic`, `LLM_ENDPOINT: https://my-foundry.services.ai.azure.com`, `LLM_MODEL: claude-sonnet-5-5`, `LLM_AUTH: entra`, no key. `values-anthropic.yaml` / `values-openai.yaml` → `llmApiKey.existingSecret: k8s-servant-llm`, `LLM_AUTH: api-key`, a header comment with the `kubectl create secret generic ... --from-file=api-key=./key.txt` command, no WI. `values-ollama.yaml` → `LLM_PROVIDER: ollama`, `LLM_ENDPOINT: http://ollama.ollama.svc:11434/v1`, `LLM_MODEL: qwen3`, no key, no WI.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_helm_chart.py
def test_old_foundry_key_in_appconfig_fails_with_new_name(): ...  # helm template fails, stderr mentions LLM_MODEL
def test_llm_api_key_in_appconfig_is_rejected(): ...
def test_endpoint_required_only_for_foundry_and_openai_compatible(): ...  # anthropic without LLM_ENDPOINT renders
def test_existing_secret_sets_llm_api_key_file(): ...


# tests/test_examples.py
@pytest.mark.parametrize("name", ["anthropic", "openai", "ollama", "azure-claude"])
def test_new_example_renders_without_creating_secrets(name): ...
def test_azure_claude_uses_entra_and_workload_identity(): ...
def test_key_examples_mount_the_key_file(): ...  # anthropic, openai
def test_ollama_example_has_no_key_and_no_workload_identity(): ...
```

- [ ] **Step 2–4:** run (fail), implement, run `.venv/bin/pytest -q && helm lint charts/k8s-servant` and `helm template` for each example.
- [ ] **Step 5: Commit**: `git commit -m "feat(chart)!: LLM_* settings and examples for every provider (#2)"`

---

### Task 8: Documentation

**Files:**
- Create: `docs/model-providers.md`
- Modify: `README.md` (provider choice table near the top, linking to the recipes; examples table lists all seven files; configuration table already renamed in Task 2), `docs/foundry-setup.md` (`LLM_AUTH=entra` + `disableLocalAuth: true`; cross-tenant case), `docs/foundry-concepts.md`, `docs/architecture.md`, `docs/cli.md`, `docs/deployment.md`, `docs/security.md`, `docs/troubleshooting.md`, `docs/data-processing.md` (Responses `store=false`; Anthropic and other providers: link to each provider's data policy), `docs/technology.md`, `docs/roadmap.md` (#2 done), `scripts/setup-foundry.sh`, `scripts/setup-workload-identity.sh` (printed settings use `LLM_*`), `tests/test_setup_foundry_script.py`, `tests/test_setup_workload_identity_script.py`
- Test: `tests/test_docs.py`

`docs/model-providers.md` sections, in order: "Why three APIs" (plain words: reasoning plus tools, what each API carries back); "Choose a provider" table (provider → API → sign-in → reasoning carried → prompt caching → token counts); one recipe each for Foundry (GPT and other Responses models), Claude on Foundry, OpenAI, Anthropic, Ollama (laptop and in-cluster), and `openai-compatible` with vLLM, LiteLLM and Gemini (`https://generativelanguage.googleapis.com/v1beta/openai/`) worked examples. Every recipe has: when to choose it, a laptop `.env` snippet, the chart values file, how to supply the key or set up Entra ID, the expected `doctor` line. Then "Settings each provider ignores" and "Migrating from FOUNDRY_* settings" (the seven-row table).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_docs.py
def test_model_providers_page_has_a_recipe_per_provider():
    page = (ROOT / "docs/model-providers.md").read_text()
    for heading in ["Foundry", "Claude on Foundry", "OpenAI", "Anthropic", "Ollama", "OpenAI-compatible"]:
        assert re.search(rf"^## .*{re.escape(heading)}", page, re.MULTILINE), heading
    for old, new in OLD_SETTINGS.items():
        assert f"`{old}`" in page and f"`{new}`" in page


def test_every_example_values_file_is_listed_in_the_readme(): ...
def test_no_doc_mentions_old_foundry_settings_outside_the_migration_table(): ...
```

- [ ] **Step 2–4:** run (fail), write the docs, run the whole suite (the existing link checker covers new links).
- [ ] **Step 5: Leak gate, then commit**

```bash
grep -rinFf <scratchpad>/denylist.txt -- $(git diff --name-only main) ; gitleaks detect --no-banner
git commit -m "docs: model providers page and LLM_* settings everywhere (#2)"
```

---

## Finish

- [ ] Full suite, lint, `helm lint`, every example rendered, `docker build -t k8s-servant:dev .` and `docker run --rm k8s-servant:dev doctor` with `LLM_PROVIDER=ollama LLM_MODEL=x` (config row OK, model row FAIL to connect: proves the image starts without azure imports breaking).
- [ ] Optional live smoke test, maintainer's keys, never committed: `k8s-servant doctor` and one `ask` with Anthropic and with OpenAI.
- [ ] Open the PR `feat!: support multiple model providers (#2)` with the repo template, `Closes #2`.
