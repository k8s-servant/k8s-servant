# Model providers

k8s-servant works with the model you already have: Azure AI Foundry, OpenAI, Anthropic
(Claude), a self-hosted model through Ollama, or any other server that speaks the
OpenAI Chat Completions API (vLLM, LiteLLM, LM Studio, Gemini, ...). You choose the
provider with `LLM_PROVIDER`; this page explains the choice and gives a complete recipe
for each one: when to pick it, the `.env` for your laptop, the Helm values file for a
cluster, how to supply the key (or set up Entra ID), and what `doctor` should print.

No provider is the "main" one. Foundry is listed first only because the first release
supported nothing else.

- [Why three APIs](#why-three-apis)
- [Choose a provider](#choose-a-provider)
- [Foundry (GPT and other Responses models)](#foundry-gpt-and-other-responses-models)
- [Claude on Foundry](#claude-on-foundry)
- [OpenAI](#openai)
- [Anthropic](#anthropic)
- [Ollama](#ollama-laptop-and-in-cluster)
- [OpenAI-compatible (vLLM, LiteLLM, Gemini, ...)](#openai-compatible-vllm-litellm-gemini-)
- [Settings each provider ignores](#settings-each-provider-ignores)
- [Migrating from FOUNDRY_* settings](#migrating-from-foundry_-settings)

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
on it (OpenAI: from GPT-5.4 on, Chat Completions does not support tool calling with a
reasoning effort other than `none`; Microsoft documents the same for the `gpt-5.6`
models on Azure). The two newer APIs do have a place for them. They hand the hidden steps
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
  `minimal` is sent as `low` and `none` turns thinking off.
- `LLM_MAX_OUTPUT_TOKENS` (default `32000`, thinking included) is sent only on the
  Messages API, which requires a limit. A reply that is cut off by a limit is an
  error and the run is recorded as failed, as is a failed, cancelled or
  content-filtered reply. Raise `LLM_MAX_OUTPUT_TOKENS` or lower the effort.
- `temperature` is never sent (reasoning models reject it).
- The full settings table is in the [README](../README.md#configuration). To start from
  a ready-made Helm values file, copy one from `examples/` (listed in each recipe).

Every recipe below installs with plain Helm. Flux or another GitOps tool works the same
way: see [`examples/flux-helmrelease.yaml`](../examples/flux-helmrelease.yaml) if you
prefer it.

## Foundry (GPT and other Responses models)

**Choose it when** your organisation runs on Azure and you want a model inside your own
tenant, in an EU or US data zone, with no key to manage: on AKS the pod signs in with
Workload Identity. It serves GPT models and others (for example DeepSeek, Llama, Grok)
through the Responses API. Partner models that offer only Chat Completions use
[`openai-compatible`](#openai-compatible-vllm-litellm-gemini-) with a Foundry endpoint,
and Claude has [its own recipe](#claude-on-foundry).

The Azure side (resource, deployment, roles, network) is in
[foundry-setup.md](foundry-setup.md) and the background in
[foundry-concepts.md](foundry-concepts.md); `scripts/setup-foundry.sh` does it in one go.

Laptop `.env` (sign in with `az login`; no key; needs the `azure` extra):

```bash
LLM_PROVIDER=foundry
LLM_ENDPOINT=https://my-foundry.openai.azure.com/   # /openai/v1/ is added for you
LLM_MODEL=my-deployment                              # the deployment name
LLM_AUTH=entra
```

Cluster: [`examples/values-azure.yaml`](../examples/values-azure.yaml) (Workload Identity,
Teams webhook synced from Key Vault by External Secrets) or
[`examples/values-azure-csi.yaml`](../examples/values-azure-csi.yaml) (no Kubernetes
Secrets at all). The relevant part:

```yaml
azureWorkloadIdentity:
  enabled: true
  clientId: "00000000-0000-0000-0000-000000000000"
  tenantId: "00000000-0000-0000-0000-000000000000"
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: foundry
  LLM_ENDPOINT: https://my-foundry.openai.azure.com/openai/v1/
  LLM_MODEL: my-deployment
  LLM_AUTH: entra
```

Entra ID setup: run `scripts/setup-workload-identity.sh` (identity, federated credential,
the *Cognitive Services OpenAI User* role on the Foundry resource), put the client and
tenant IDs it prints into `azureWorkloadIdentity`, then follow
[foundry-setup.md](foundry-setup.md). With `LLM_AUTH=entra` no key is ever needed, and you
can set `disableLocalAuth: true` on the resource so keys cannot be used at all.

If you must use a key instead (a quick experiment, or another tenant), create the Secret
as in [OpenAI](#openai), set `LLM_AUTH: api-key` and `llmApiKey.existingSecret`.

Expected `doctor` model line:

```
model: foundry / my-deployment via Responses API, Entra ID (Workload Identity)
```

On a laptop the source reads `Entra ID (az login / DefaultAzureCredential)`.

## Claude on Foundry

**Choose it when** you want Claude but inside Azure: same Entra ID sign-in and billing as
the Foundry recipe, with Claude's Messages API. Deploy a Claude model in your Foundry
resource first ([foundry-setup.md](foundry-setup.md), Step 2, choose a Claude model).

Laptop `.env`:

```bash
LLM_PROVIDER=anthropic
LLM_ENDPOINT=https://my-foundry.services.ai.azure.com   # /anthropic is added for you
LLM_MODEL=claude-sonnet-5-5                              # the deployment name
LLM_AUTH=entra
```

Cluster: [`examples/values-azure-claude.yaml`](../examples/values-azure-claude.yaml).

```yaml
azureWorkloadIdentity:
  enabled: true
  clientId: "00000000-0000-0000-0000-000000000000"
  tenantId: "00000000-0000-0000-0000-000000000000"
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: anthropic
  LLM_ENDPOINT: https://my-foundry.services.ai.azure.com
  LLM_MODEL: claude-sonnet-5-5
  LLM_AUTH: entra
```

Entra ID setup is the same as for [Foundry](#foundry-gpt-and-other-responses-models):
`scripts/setup-workload-identity.sh`, then the identity's client and tenant IDs in
`azureWorkloadIdentity`. Entra ID is accepted for `anthropic` only because the endpoint
is a Foundry address.

Expected `doctor` model line:

```
model: anthropic / claude-sonnet-5-5 via Messages API, Entra ID (Workload Identity)
```

## OpenAI

**Choose it when** you have an OpenAI account (a ChatGPT subscription is not an API
account) and want the shortest path, on a laptop or in any cluster. It uses the Responses
API.

Laptop `.env`:

```bash
LLM_PROVIDER=openai
LLM_MODEL=gpt-5
LLM_API_KEY_FILE=/path/to/openai-api-key   # or LLM_API_KEY=... for a quick test
```

No `LLM_ENDPOINT` is needed (it defaults to `https://api.openai.com/v1`).

Cluster: [`examples/values-openai.yaml`](../examples/values-openai.yaml). Put the key in a
file named `api-key` and create the Secret (this keeps it out of your shell history):

```bash
kubectl create namespace k8s-servant
kubectl -n k8s-servant create secret generic k8s-servant-llm --from-file=./api-key
rm ./api-key
helm install k8s-servant oci://ghcr.io/k8s-servant/charts/k8s-servant \
  -n k8s-servant -f examples/values-openai.yaml
```

```yaml
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: openai
  LLM_MODEL: gpt-5
  LLM_AUTH: api-key
llmApiKey:
  existingSecret: k8s-servant-llm
```

The chart mounts the Secret as the file `/var/run/secrets/llm/api-key` and sets
`LLM_API_KEY_FILE` for you; the key is never an environment variable. External Secrets,
Sealed Secrets or the Secrets Store CSI driver work just as well, as long as a Secret or
file with the key exists ([deployment.md](deployment.md#credentials-bring-your-own-secrets)).

Expected `doctor` model line:

```
model: openai / gpt-5 via Responses API, API key (file /var/run/secrets/llm/api-key)
```

## Anthropic

**Choose it when** you want Claude directly from Anthropic, on a laptop or in any cluster,
with prompt caching (the large system prompt is repeated on every run, so caching saves
cost and time). It uses the Messages API.

Laptop `.env`:

```bash
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-5-5
LLM_API_KEY_FILE=/path/to/anthropic-api-key   # or LLM_API_KEY=... for a quick test
```

No `LLM_ENDPOINT` is needed (it defaults to `https://api.anthropic.com`).

Cluster: [`examples/values-anthropic.yaml`](../examples/values-anthropic.yaml). Create the
Secret exactly as for [OpenAI](#openai) (a file named `api-key`, secret
`k8s-servant-llm`), then:

```yaml
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: anthropic
  LLM_MODEL: claude-sonnet-5-5
  LLM_AUTH: api-key
llmApiKey:
  existingSecret: k8s-servant-llm
```

Expected `doctor` model line:

```
model: anthropic / claude-sonnet-5-5 via Messages API, API key (file /var/run/secrets/llm/api-key)
```

Reasoning effort maps onto Anthropic's effort levels (`minimal` is sent as `low`, `none`
switches thinking off). If a reply hits the output limit the run fails with a message
naming `LLM_MAX_OUTPUT_TOKENS`.

## Ollama (laptop and in-cluster)

**Choose it when** the cluster data must not leave your network, or you want to try the
agent for free. Quality depends on the model: it must support tool calling (for example
`qwen3`), and small models find fewer problems than the hosted ones. It uses the Chat
Completions API, needs no key, and does not send a reasoning effort.

**Laptop** (Ollama running locally):

```bash
ollama pull qwen3
```

```bash
LLM_PROVIDER=ollama
LLM_MODEL=qwen3
# LLM_ENDPOINT defaults to http://localhost:11434/v1
```

**In the cluster**: run Ollama as a Deployment with a Service (any way you like; the
model must be pulled into it), then use
[`examples/values-ollama.yaml`](../examples/values-ollama.yaml):

```yaml
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: ollama
  LLM_ENDPOINT: http://ollama.ollama.svc:11434/v1
  LLM_MODEL: qwen3
```

Plain `http` is accepted for `localhost` and for in-cluster hosts (a name without dots,
`*.svc` or `*.svc.cluster.local`), by design: a model in the same cluster is the main
use case. Anything else must be https.

> [!WARNING]
> Over plain `http` nothing is encrypted. If you do set an API key for such an endpoint
> (for example behind a proxy that checks it), the key travels **unencrypted inside the
> cluster network**. Prefer an https endpoint, or use no key. The cluster data the
> agent sends (object names, events, log lines) is unencrypted on that hop too.
> Entra ID tokens never go over http: Foundry hosts are always https.

Expected `doctor` model line:

```
model: ollama / qwen3 via Chat Completions API, no key
```

## OpenAI-compatible (vLLM, LiteLLM, Gemini, ...)

**Choose it when** your server speaks the OpenAI Chat Completions API and is not one of
the providers above: self-hosted vLLM or LM Studio, a LiteLLM gateway in front of many
models, Google Gemini's OpenAI-compatible endpoint, or a partner model on Foundry that
only offers Chat Completions. `LLM_ENDPOINT` is the full base URL including its path
(usually ending in `/v1`). A key is sent only if you configure one. The reasoning effort
is not sent unless you set `LLM_REASONING_EFFORT`, because the model behind the endpoint
is unknown and many reject it. The model must support tool calling.

Cluster base: [`examples/values-minimal.yaml`](../examples/values-minimal.yaml) is exactly
this provider with an API key and a Teams webhook:

```yaml
appConfig:
  CLUSTER_NAME: my-cluster
  LLM_PROVIDER: openai-compatible
  LLM_ENDPOINT: https://api.example.com/v1
  LLM_MODEL: my-model
  LLM_AUTH: api-key
llmApiKey:
  existingSecret: llm-api-key
```

Create the key Secret from a file named `api-key`:
`kubectl -n k8s-servant create secret generic llm-api-key --from-file=./api-key`.
For an endpoint without a key, drop `LLM_AUTH` and `llmApiKey`. Worked examples:

**vLLM** (in the cluster, no key):

```bash
LLM_PROVIDER=openai-compatible
LLM_ENDPOINT=http://vllm.inference.svc:8000/v1
LLM_MODEL=Qwen/Qwen3-32B
```

Start vLLM with tool calling enabled (`--enable-auto-tool-choice` and a
`--tool-call-parser` for the model); without it the agent gets no tool calls.

**LiteLLM gateway** (with a virtual key):

```bash
LLM_PROVIDER=openai-compatible
LLM_ENDPOINT=https://litellm.example.com/v1
LLM_MODEL=gpt-5            # a model name configured in LiteLLM
LLM_API_KEY_FILE=/path/to/litellm-key
```

**Gemini**:

```bash
LLM_PROVIDER=openai-compatible
LLM_ENDPOINT=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-2.5-pro
LLM_API_KEY_FILE=/path/to/gemini-key
```

**Partner model on Foundry** that offers only Chat Completions: use
`LLM_ENDPOINT=https://my-foundry.services.ai.azure.com/openai/v1/` (or your resource's
address) and `LLM_AUTH=entra`; Entra ID is accepted because the endpoint is a Foundry
address.

> [!WARNING]
> As with Ollama: an in-cluster `http` endpoint is allowed, but a key sent to it travels
> **unencrypted inside the cluster network**. Use https, or no key.

Expected `doctor` model lines:

```
model: openai-compatible / my-model via Chat Completions API, API key (file /var/run/secrets/llm/api-key)
model: openai-compatible / Qwen/Qwen3-32B via Chat Completions API, no key
```

## Settings each provider ignores

A setting a provider has no use for has no effect; the agent does not warn about it.

| Setting | Has no effect for |
|---|---|
| `LLM_TOKEN_SCOPE` | every sign-in except Entra ID (so: anything that uses a key or no sign-in) |
| `LLM_MAX_OUTPUT_TOKENS` | `foundry`, `openai`, `ollama`, `openai-compatible`: it is sent only on the Messages API (`anthropic`), which requires a limit |
| `LLM_REASONING_EFFORT` | nothing, but it is empty (not sent) by default for `ollama` and `openai-compatible`; set it only if the model behind the endpoint accepts it, and keep it empty for models that reject the parameter |
| `LLM_API_KEY`, `LLM_API_KEY_FILE` | `LLM_AUTH=entra` refuses to start if one is set. `ollama` needs none, but a key you do set is sent |
| `LLM_ENDPOINT` | `openai`, `anthropic` and `ollama` have a default; `foundry` and `openai-compatible` require it |

## Migrating from FOUNDRY_* settings

The settings of the single-provider releases were renamed, with no aliases. If an old
name is still set, the agent refuses to start and names the new one. The Helm chart
refuses an old `appConfig` key the same way. Also set `LLM_PROVIDER` (new and required);
use `foundry` for what you had before.

| Before | Now |
|---|---|
| `FOUNDRY_ENDPOINT` | `LLM_ENDPOINT` |
| `FOUNDRY_DEPLOYMENT` | `LLM_MODEL` |
| `FOUNDRY_API_KEY` | `LLM_API_KEY` |
| `FOUNDRY_API_KEY_FILE` | `LLM_API_KEY_FILE` |
| `FOUNDRY_TOKEN_SCOPE` | `LLM_TOKEN_SCOPE` |
| `FOUNDRY_TIMEOUT_SECONDS` | `LLM_TIMEOUT_SECONDS` |
| `FOUNDRY_REASONING_EFFORT` | `LLM_REASONING_EFFORT` |

New settings: `LLM_PROVIDER`, `LLM_AUTH` and `LLM_MAX_OUTPUT_TOKENS`. In Helm values the
keys under `appConfig` are renamed the same way; `llmApiKey` is unchanged.

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
