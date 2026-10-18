# Multi-provider model support: design

Issue: #2. Status: draft for maintainer approval (builder check done). Date: 2026-10-18.

## 1. Intent

k8s-servant only talks to MS Foundry today. People should be able to run it with the
model they already have: Claude or ChatGPT on a laptop for testing, and, where the real
value is, in any cluster (local, AKS, GKE, EKS) with Foundry, OpenAI, Anthropic or a
self-hosted model. MS Foundry stays the main, documented example.

Why this is more than "add a URL setting": the API we use today (OpenAI **Chat
Completions**) no longer works for our tool loop on current GPT models.

- Microsoft Learn, *Azure OpenAI reasoning models*: "The `gpt-5.6` models support the
  Chat Completions API and tools, but can't combine reasoning with tools on Chat
  Completions." A request with `tools` fails even when `reasoning_effort` is not sent,
  because these models default to `medium`. Microsoft recommends the Responses API.
- OpenAI, *Migrate to Responses*: "Starting with GPT-5.4, Chat Completions does not
  support tool calling with `reasoning_effort` values other than `none`."
- Anthropic's OpenAI-compatible endpoint is "not considered a long-term or
  production-ready solution". On it, `reasoning_effort` is ignored, prompt caching is
  not supported, `strict` is ignored, and cached-token counts are always empty.

The reason vendors are moving: reasoning models think in hidden steps, and in a tool
loop they need those hidden steps carried from one tool call to the next. Chat
Completions has no place for them. The Responses API (OpenAI, Foundry) and the Messages
API (Anthropic) do: they return the reasoning in an opaque, encrypted or signed form
that the client sends back unchanged.

## 2. Decisions (made by the maintainer)

- **Three APIs**, one API client each, behind one neutral interface:

  | `LLM_PROVIDER` | API | Sign-in |
  |---|---|---|
  | `foundry` | Responses API | Entra ID or API key |
  | `openai` | Responses API | API key |
  | `anthropic` | Messages API (native `anthropic` SDK) | API key; Entra ID when the endpoint is Foundry |
  | `ollama` | Chat Completions API | none |
  | `openai-compatible` | Chat Completions API | API key (optional); Entra ID when the endpoint is Foundry |

  The provider decides the API. There is no setting to choose the API directly.
- **`ollama` is a named provider**, not only part of the catch-all.
- **Sign-in in this issue: API key and Entra ID.** Entra ID works for every model hosted
  on Foundry, whatever its API: GPT, DeepSeek, Llama, Grok (`foundry`, Responses API),
  Claude on Foundry (`anthropic` with a Foundry endpoint, Messages API) and partner models
  that only offer Chat Completions (`openai-compatible` with a Foundry endpoint). AWS (Bedrock) and GCP
  (Vertex AI) identity come later, in their own issue; this design must leave room for
  them without another rename.
- **Rename now, no aliases.** `FOUNDRY_*` settings become `LLM_*`. Breaking changes are
  acceptable: the project is new and has no known users. Released as a breaking change
  (pre-1.0, so a minor bump) with a migration table in the release notes and docs.
- **`LLM_AUTH=auto|entra|api-key`, default `auto`** (decided 2026-10-17, recorded in #2):
  - `auto`: key if present, else Entra ID; **warn** when Workload Identity is detected
    (`AZURE_FEDERATED_TOKEN_FILE` set) and a key is also configured.
  - `entra`: strict; refuses to start if a key is configured.
  - `api-key`: strict; refuses to start without a key.
  - `entra` is valid with `foundry`, and with `anthropic` or `openai-compatible` when
    `LLM_ENDPOINT` is a Foundry address (host ending in `.openai.azure.com`,
    `.services.ai.azure.com` or `.cognitiveservices.azure.com`). Any other combination is
    a configuration error. Under `auto`, Entra ID is tried without a key only in those
    same cases; elsewhere a missing key (except `ollama`) is a configuration error.
- **Well documented, and not Foundry-only**: a "Model providers" page that explains, in
  plain words, why there are three APIs, and gives a full recipe for every provider.
  Foundry is listed first, but every provider gets an equally complete example, plus a
  ready-to-use chart values file, so nobody reads the project as Azure-only.
- Docs and code say **API**, not metaphors.

## 3. Goals

- G1. Foundry with Entra ID works as before, now on the Responses API, so current GPT
  deployments (gpt-5.6 and later) work with reasoning and tools together.
- G2. OpenAI, Anthropic and Ollama work and are documented; `openai-compatible` covers
  vLLM, LM Studio, LiteLLM, Gemini and others on Chat Completions.
- G3. The agent loop (`agent.py`), health check, report and memory do not know which
  provider is in use.
- G4. Reasoning is carried across tool calls on the Responses and Messages APIs.
- G5. Prompt caching works on Anthropic (our system prompt is large and repeated every
  run); cached-token counts are logged for every provider that reports them.
- G6. `doctor` names the provider, API, model and sign-in method with its source, and
  makes a real test call.
- G7. Settings fail fast with clear messages on a missing or invalid combination.

## 4. Non-goals

- Bedrock / Vertex sign-in (later issue; see §7).
- Per-run cost caps, model routing (different models per task), streaming, fallback to
  a second provider.
- Exposing reasoning text to users or logs.
- A setting that picks the API independently of the provider.

## 5. Anti-goals (would be a failure even if it works)

- Logging an API key or putting it in an error message, anywhere.
- Silent degradation: a provider that drops a setting (for example reasoning effort)
  without saying so once at startup.
- Provider types leaking into `agent.py`, `healthcheck.py`, `report.py` or tests outside
  the LLM module's own tests.
- Sending `temperature` to any model.
- Storing conversations at the provider: the Responses API must be called with
  `store=false` (our data-processing doc promises no provider-side state).

## 6. Design

### 6.1 Settings

All read through `config.configure_app()`, one comment per key.

| Setting | Meaning | Default |
|---|---|---|
| `LLM_PROVIDER` | `foundry`, `openai`, `anthropic`, `ollama`, `openai-compatible` | none, required |
| `LLM_ENDPOINT` | Base URL | required for `foundry` and `openai-compatible`; built-in for `openai` (`https://api.openai.com/v1`), `anthropic` (`https://api.anthropic.com`), `ollama` (`http://localhost:11434/v1`) |
| `LLM_MODEL` | Model name; on Foundry the deployment name | required |
| `LLM_API_KEY_FILE` / `LLM_API_KEY` | Key, file wins (existing `_secret()` helper) | empty |
| `LLM_AUTH` | `auto`, `entra`, `api-key` | `auto` |
| `LLM_TOKEN_SCOPE` | Entra token scope (Foundry) | `https://ai.azure.com/.default` |
| `LLM_REASONING_EFFORT` | `""` (not sent), `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` | `medium` for `foundry`, `openai`, `anthropic`; `""` for `ollama` and `openai-compatible` *(writer's call: the model behind those endpoints is unknown and many local models reject the parameter)* |
| `LLM_TIMEOUT_SECONDS` | Per-request timeout | `120` |
| `LLM_MAX_OUTPUT_TOKENS` | Output limit per call, thinking included. Sent only on the Messages API, which requires it; the Responses and Chat Completions APIs keep today's "no limit" | `32000` *(writer's call)* |

Rules:
- `normalize_endpoint` keeps today's behaviour for Foundry (bare host gets `/openai/v1`)
  and the https-only rule (http only for localhost). **Exception for `ollama` and
  `openai-compatible`:** plain http is also accepted for in-cluster Service names
  (single-label or `*.svc` / `*.svc.cluster.local` hosts), since a self-hosted model in
  the same cluster is a main use case. Anything else over http is refused.
- `LLM_AUTH=entra` outside the cases in §2: configuration error naming the rule.
- `ollama` needs no key; `openai-compatible` sends a key only if one is set.
- Reasoning effort is mapped per API (§6.3). A value the provider cannot honour logs one
  warning at startup and is dropped, never silently.
- A reply cut off by a limit (`incomplete` on Responses, `stop_reason=max_tokens` on
  Messages, `finish_reason=length` on Chat Completions) raises a model error whose hint
  names `LLM_MAX_OUTPUT_TOKENS` / the reasoning effort; it is recorded as a failed run,
  never treated as an answer. *(Writer's call.)*
- The old `FOUNDRY_*` names are not read. If any is set, startup fails with a message
  naming the new setting (cheap, avoids a confusing "missing LLM_MODEL"). *(Writer's
  call; can be removed in a later release.)*

### 6.2 Neutral interface

`agent.py` today passes Chat Completions dicts and receives `ChatCompletionMessage`.
Replace this with small neutral types owned by the LLM package:

- `Turn`: role (`system`, `user`, `assistant`, `tool`), text, tool calls (id, name,
  JSON arguments), tool-call id for tool results, and an opaque `provider_state` field.
- `provider_state` carries what the API needs back unchanged (Responses: reasoning items
  with `encrypted_content`; Messages: thinking blocks with signatures). The agent stores
  and returns it but never reads it.
- `ChatModel.complete(turns, tools) -> Turn`, tools given as the registry's neutral
  definitions (name, description, JSON schema). Each API client converts.
- `total_tokens`, and a log line per call with prompt, cached, completion and reasoning
  tokens (whatever the API reports).

`AgentResult.messages` becomes a list of `Turn`, so `follow_up()` and the health check's
JSON retry keep working unchanged in behaviour. `tests/fakes.py` builds `Turn`s.

### 6.3 API clients

One module per API under `k8s_servant/llm/`, plus a factory that picks by provider.

- **Responses** (`foundry`, `openai`): `openai` SDK `client.responses.create`, with
  `store=False`, `include=["reasoning.encrypted_content"]`, `reasoning={"effort": ...}`.
  Reasoning items from each reply go into `provider_state` and are sent back on the next
  call. Entra ID via `azure-identity` token provider (today's code), API key otherwise.
- **Messages** (`anthropic`): native `anthropic` SDK. System turns go to the top-level
  `system` field. Prompt caching on (automatic top-level `cache_control`). Thinking is
  enabled and its effort follows `LLM_REASONING_EFFORT`, mapped to Anthropic's current
  effort control; thinking blocks are kept in `provider_state` and sent back during tool
  use, as the API requires. `tool_choice` stays `auto` (current Claude models reject
  forced tool use). With a Foundry endpoint and Entra ID, the SDK's Foundry client is used
  with the same `azure-identity` token provider. When the history holds tool calls, the tool definitions are sent
  on every call (the API requires it); calls that must not use tools (iteration limit,
  `follow_up`, the morning overview) send them with `tool_choice: none`.
- **Chat Completions** (`ollama`, `openai-compatible`): today's code, moved, including
  its Entra ID token provider for Foundry endpoints. Sends `reasoning_effort` only when set.

Error hints (`explain_model_error`) become per provider: Foundry keeps today's hints
(role assignment, private endpoint, deployment name); others get generic but accurate
ones (bad key, unknown model, rate limit, cannot reach endpoint) naming `LLM_*` settings.
Never include the key.

### 6.4 `doctor`

One line, then the test call. Examples:
- `model: foundry / my-deployment via Responses API, Entra ID (Workload Identity)`
- `model: anthropic / claude-sonnet-5-5 via Messages API, API key (file /var/run/secrets/llm/api-key)`
- `model: ollama / qwen3 via Chat Completions API, no key`

The `auto` warning (Workload Identity plus key) also shows here.

### 6.5 Dependencies and image

- `openai` stays a core dependency; `anthropic` is added as core (small, pure Python).
- `azure-identity` becomes an optional extra (`k8s-servant[azure]`); the container image
  installs it. Choosing `foundry` with Entra ID without the extra fails with a message
  saying to install it.

### 6.6 Chart

- `appConfig` keys renamed (`LLM_PROVIDER`, `LLM_ENDPOINT`, `LLM_MODEL`, `LLM_AUTH`, ...).
  `values.yaml` defaults to `LLM_PROVIDER: foundry` to keep the documented path short;
  the app itself has no default.
- `llmApiKey.existingSecret` / `llmApiKey.file` stay; they now set `LLM_API_KEY_FILE`.
  The guard that rejects `FOUNDRY_API_KEY` in `appConfig` rejects `LLM_API_KEY` instead.
- `values-azure.yaml`, `values-azure-csi.yaml`: `LLM_PROVIDER: foundry`, `LLM_AUTH: entra`.
- New example values files, one per common setup, each rendered and checked by
  `tests/test_examples.py`:
  - `values-anthropic.yaml`: Claude via Anthropic, key from a plain Secret
    (`kubectl create secret ... --from-file`), no cloud identity;
  - `values-openai.yaml`: OpenAI, key from a plain Secret;
  - `values-ollama.yaml`: a self-hosted model through an in-cluster Ollama Service, no key;
  - `values-azure-claude.yaml`: Claude on Foundry with Workload Identity (`anthropic`,
    Foundry endpoint, `LLM_AUTH: entra`).
  `values-minimal.yaml` is updated to the new names. The README's examples table lists
  all of them.

### 6.7 Documentation

- New `docs/model-providers.md`, Foundry first:
  1. Why three APIs, in plain words (thinking plus tools; what each API carries).
  2. Table: provider → API → sign-in → reasoning carried, prompt caching, token counts.
  3. A full recipe per provider, each with: when to choose it, a laptop `.env` snippet,
     the matching chart values file, how to get and mount the key (or set up Entra), and
     what to check in `doctor`. Providers: Foundry (GPT and other Responses models, link to
     `foundry-setup.md` for the Azure side), Claude on Foundry, OpenAI, Anthropic, Ollama
     (laptop and in-cluster), and `openai-compatible` with worked examples for vLLM,
     LiteLLM and Gemini.
  4. Which settings each provider ignores.
- Migration table (`FOUNDRY_*` → `LLM_*`) in the docs and the release notes.
- README and the quick-start show a provider choice up front (a short table linking to
  the recipes), not only Foundry. Full README rewrite stays with #11.
- Update every page that names `FOUNDRY_*`: README, architecture, cli, deployment,
  foundry-concepts, foundry-setup, security, troubleshooting, data-processing (Responses
  with `store=false`; Anthropic's data handling), technology, `.env.example`, chart
  values comments. `scripts/setup-foundry.sh` and `scripts/setup-workload-identity.sh`
  print the new names.
- `foundry-setup.md` recommends `LLM_AUTH=entra` plus `disableLocalAuth: true` on the
  Foundry resource, and documents the cross-tenant case (Workload Identity for Key Vault,
  key for Foundry).
- `tests/test_docs.py` keeps checking that every setting in code is documented.

### 6.8 Tests (TDD, one test file per module)

- Each API client against a recorded/faked SDK response: text reply, tool call, reasoning
  state carried into the next request, usage logging, no `temperature`, `store=False`
  (Responses), cache control present (Messages).
- Settings: every provider's defaults; required settings; each `LLM_AUTH` mode including
  strict failures and the `auto` warning; `entra` accepted for Foundry endpoints on
  `foundry`, `anthropic` and `openai-compatible`, rejected elsewhere;
  old `FOUNDRY_*` names rejected with a pointer; http rules.
- The key never appears in logs, errors or `doctor` output (all providers).
- Agent, health check and report tests pass using neutral `Turn` fakes only.
- Chart and example tests for the new names and every new example values file.

## 7. Room for later (not built now)

- **Bedrock / Vertex:** a new `LLM_AUTH` value per cloud identity (`aws`, `gcp`) and a
  provider (`bedrock`, `vertex`). Claude on either is the Messages API through the
  `anthropic` SDK's Bedrock/Vertex clients, so it reuses the Messages client.
- **Gemini** as a named provider: a default endpoint in the provider table, no new code.
- Cost caps and model routing sit above `ChatModel` and need no API-client change.

## 8. Left to the builder

- Exact module layout and type names inside `k8s_servant/llm/`.
- How the Responses and Messages SDK objects are faked in tests.
- Mapping tables for reasoning effort per API, from the current vendor docs.
- Wording of error hints and docs, within the rules above.
