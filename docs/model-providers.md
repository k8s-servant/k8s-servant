# Model providers

k8s-servant works with the model you already have: Azure AI Foundry, OpenAI, Anthropic
(Claude), a self-hosted model through Ollama, or any other server that speaks the
OpenAI Chat Completions API (vLLM, LiteLLM, LM Studio, Gemini, ...). You choose the
provider with `LLM_PROVIDER`. This page explains the choice and the rules every
provider shares; each provider then has its own recipe: when to pick it, the `.env`
for your laptop, the Helm values file for a cluster, how to supply the key (or set up
Entra ID), and what `doctor` should print.

| Recipe | `LLM_PROVIDER` | Sign-in |
|---|---|---|
| [Anthropic](providers/anthropic.md) | `anthropic` | API key |
| [OpenAI](providers/openai.md) | `openai` | API key |
| [Ollama](providers/ollama.md), on a laptop or in the cluster | `ollama` | none |
| [OpenAI-compatible](providers/openai-compatible.md): vLLM, LiteLLM, Gemini, LM Studio, ... | `openai-compatible` | API key (optional) |
| [GPT and other models on Azure AI Foundry](providers/foundry/gpt.md) | `foundry` | Entra ID or API key |
| [Claude on Azure AI Foundry](providers/foundry/claude.md) | `anthropic` | Entra ID |

No provider is the "main" one. Azure AI Foundry also has an
[overview](providers/foundry/index.md), a [concepts page](providers/foundry/concepts.md)
and a [step-by-step setup guide](providers/foundry/setup.md).

On this page:

- [Why three APIs](#why-three-apis)
- [Choose a provider](#choose-a-provider)
- [Settings each provider ignores](#settings-each-provider-ignores)
- [Data handling and errors](#data-handling-and-errors)

## Why three APIs

An **API** here is the set of rules for sending a request to a model and reading its
reply. The agent speaks three of them, one per family of providers. You never choose an
API directly: the provider decides.

The reason there is more than one is how newer models work. A reasoning model thinks in
hidden steps before it answers. The agent works in a loop: the model asks for a tool
(for example "list the pods"), the agent runs it and sends the result back, and the model
asks for the next tool. In that loop the model needs its own hidden steps from the
previous turn, otherwise it forgets why it asked for the tool and works worse.

The older **Chat Completions API** has no place to carry those hidden steps from one tool
call to the next. The newest GPT models therefore refuse to combine tools with reasoning
on it, and the exact cut-off differs by platform:

- **OpenAI API:** "Starting with GPT-5.4, Chat Completions does not support tool calling
    with `reasoning_effort` values other than `none`"
    ([OpenAI: migrate to Responses](https://developers.openai.com/api/docs/guides/migrate-to-responses)).
- **Microsoft Foundry:** the `gpt-5.6` models "support the Chat Completions API and tools,
    but can't combine reasoning with tools on Chat Completions". A request with tools fails
    even without `reasoning_effort`, because these models default to `medium`. `gpt-5.4` and
    `gpt-5.5` still allow it on Foundry
    ([Microsoft Learn](https://learn.microsoft.com/azure/foundry/openai/how-to/reasoning#tool-calling-with-reasoning-models)).

So an older deployment such as `gpt-5.4-mini` on Foundry works on Chat Completions (the
agent's first release ran that way), but moving to the Responses API keeps current and
future models working. The two newer APIs do have a place for the hidden steps. They hand the hidden steps
back in a sealed (encrypted or signed) form that the agent keeps unread and sends back
unchanged on the next call:

| API | Used by | What it carries back between tool calls |
|---|---|---|
| **Responses API** | `foundry`, `openai` | Reasoning items in encrypted form |
| **Messages API** (Anthropic's own) | `anthropic` (also Claude on Foundry) | Thinking blocks with their signatures |
| **Chat Completions API** | `ollama`, `openai-compatible` | Nothing. Fine for models that do not need it |

Anthropic also offers an OpenAI-compatible endpoint, but it describes it as not meant for
production use (reasoning effort is ignored, prompt caching is unsupported). That is why
Claude uses the Messages API.

The rest of the agent (the loop, health checks, reports, memory) does not know which API
is behind it.

## Choose a provider

| `LLM_PROVIDER` | API | Sign-in | Reasoning carried between tool calls | Prompt caching | Token counts in the log |
|---|---|---|---|---|---|
| `foundry` | Responses | Entra ID or API key | yes | automatic, on the service side | prompt, cached, completion, reasoning |
| `openai` | Responses | API key | yes | automatic, on the service side | prompt, cached, completion, reasoning |
| `anthropic` | Messages | API key; Entra ID when the endpoint is Foundry | yes | yes, requested by the agent | prompt, cached, completion |
| `ollama` | Chat Completions | none | no | none | what the server reports |
| `openai-compatible` | Chat Completions | API key (optional); Entra ID when the endpoint is Foundry | no | depends on the server | what the server reports |

Rules that hold for every provider:

- `LLM_MODEL` is required. On Foundry it is the **deployment** name.
- `LLM_AUTH` is `auto` (default), `entra` or `api-key`. `auto` uses a key when one is
    configured, otherwise Entra ID where the endpoint is a Foundry address, otherwise no
    sign-in for `ollama` and `openai-compatible`, otherwise it refuses to start.
    `entra` and `api-key` are strict: `entra` refuses to start if a key is also set,
    `api-key` refuses to start without one.
- **Entra ID is only ever used for Foundry endpoints**, so its tokens can never reach a
    third-party server. A Foundry endpoint is a host ending in `.openai.azure.com`,
    `.services.ai.azure.com` or `.cognitiveservices.azure.com`. These hosts are always
    https.
- With `auto`, a configured key wins over Workload Identity. `doctor` then prints a
    warning; set `LLM_AUTH=entra` and remove the key to use Workload Identity.
- Keys are read from `LLM_API_KEY_FILE` (a file; in the cluster the chart mounts your
    Secret there) or `LLM_API_KEY`; the file wins. The key is never logged.
- Entra ID needs the Azure extra: `pip install 'k8s-servant[azure]'`, or
    `pip install -e '.[dev,azure]'` in a clone. The container image already includes it.
- `LLM_REASONING_EFFORT` defaults to `medium` for `foundry`, `openai` and `anthropic`,
    and to empty (not sent) for `ollama` and `openai-compatible`. On Anthropic,
    `minimal` is sent as `low` (with a warning in the log, as the Messages API has no
    `minimal`) and `none` turns thinking off.
- `LLM_MAX_OUTPUT_TOKENS` (default `32000`, thinking included) is sent only on the
    Messages API, which requires a limit. A reply that is cut off by a limit is an
    error and the run is recorded as failed, as is a failed, cancelled or
    content-filtered reply. Raise `LLM_MAX_OUTPUT_TOKENS` or lower the effort.
- `temperature` is never sent (reasoning models reject it).
- The full settings table is in [configuration.md](configuration.md). To start from
    a ready-made Helm values file, copy one from `examples/` (listed in each recipe).

Every recipe installs with plain Helm. Flux or another GitOps tool works the same
way: see [`examples/flux-helmrelease.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/flux-helmrelease.yaml) if you
prefer it.

## Settings each provider ignores

A setting a provider has no use for has no effect; the agent does not warn about it.

| Setting | Has no effect for |
|---|---|
| `LLM_TOKEN_SCOPE` | every sign-in except Entra ID (so: anything that uses a key or no sign-in) |
| `LLM_MAX_OUTPUT_TOKENS` | `foundry`, `openai`, `ollama`, `openai-compatible`: it is sent only on the Messages API (`anthropic`), which requires a limit |
| `LLM_REASONING_EFFORT` | nothing, but it is empty (not sent) by default for `ollama` and `openai-compatible`; set it only if the model behind the endpoint accepts it, and keep it empty for models that reject the parameter |
| `LLM_API_KEY`, `LLM_API_KEY_FILE` | `LLM_AUTH=entra` refuses to start if one is set. `ollama` needs none, but a key you do set is sent |
| `LLM_ENDPOINT` | `openai`, `anthropic` and `ollama` have a default; `foundry` and `openai-compatible` require it |

## Data handling and errors

- The Responses API is called with `store=false`: OpenAI and Foundry do not keep the
    conversation. For Anthropic and every other provider, read the provider's own data and
    retention terms: [Anthropic privacy centre](https://privacy.anthropic.com/),
    [OpenAI API data controls](https://platform.openai.com/docs/guides/your-data),
    [Gemini API terms](https://ai.google.dev/gemini-api/terms). More in
    [data-processing.md](data-processing.md) and [security.md](security.md).
- Errors name the setting to fix and never contain the key. Foundry gets hints about
    role assignment, private endpoints and deployment names; other providers get generic
    ones (key rejected, unknown model, rate limit, cannot reach `LLM_ENDPOINT`). See
    [troubleshooting.md](troubleshooting.md).
