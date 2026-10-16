# Technology

What the agent is built with, and why each piece was chosen. Versions are the ones
tested on 2026-10-12; `pyproject.toml` holds the allowed ranges.

## At a glance

```
 Python 3.14 program (k8s_servant)                        runs on a laptop and in AKS (one pod)
 ├── openai SDK ──────── HTTPS, OpenAI Chat Completions v1 ──▶ MS Foundry: gpt-5.4-mini (DataZoneStandard)
 │     └── azure-identity: Entra ID token (az login / Workload Identity)
 ├── mcp SDK ─────────── MCP, streamable HTTP ──────────────▶ kubernetes-mcp-server (read-only) ─▶ Kubernetes API
 ├── kubectl (subprocess, allow-listed) ─────────────────────────────────────────────────────────▶ Kubernetes API
 ├── httpx2 ──────────── HTTP GET ──────────────────────────▶ Prometheus, Alertmanager
 │                       HTTP POST (Adaptive Card) ─────────▶ MS Teams Workflows webhook
 ├── sqlite3 (stdlib) ── memory.db (WAL) + notes.md ────────▶ DATA_DIR (a PersistentVolume in AKS)
 └── PyYAML ──────────── SKILL.md frontmatter, Helm values files
```

## Runtime and language

| | Version | Why |
|---|---|---|
| **Python** | 3.14 (3.12+ supported) | Widely known by platform teams; asyncio fits an agent that waits on the model, MCP, kubectl and HTTP at the same time |
| **asyncio** | stdlib | One event loop for the scheduler, tool calls, timeouts and cancellation (SIGTERM stops a running check at once) |
| **sqlite3** | stdlib | Memory needs "what is new since last time": a SQL query, no server, one file on a volume. WAL mode lets the scheduler and a person's CLI use it at once |
| **logging** | stdlib | Logs to stderr, answers to stdout |

## Python libraries

| Library | Version | Used for | Why this one |
|---|---|---|---|
| **openai** | 3.29 | Calling the model (Chat Completions, tool calling) | Foundry exposes an OpenAI-compatible **v1** endpoint (`https://<resource>.openai.azure.com/openai/v1/`), so the official SDK works without Azure-specific code or `api-version` parameters. It retries 429 and 5xx with backoff |
| **azure-identity** | 1.26 | Entra ID tokens for Foundry | `DefaultAzureCredential` takes `az login` on a laptop and Workload Identity in the pod, with no code change and no key |
| **mcp** | 2.3 | Client for kubernetes-mcp-server | The official Model Context Protocol SDK (v2 API; older v1 examples online do not apply) |
| **httpx2** | 2.13 | Prometheus, Alertmanager, Teams | The HTTP client both SDKs already use, so no second HTTP stack |
| **PyYAML** | 6.0 | Skill frontmatter, Helm values files | The knowledge is delivered as Helm values; the CLI reads the very same files |
| **tzdata** | 2026.5 | Time zones for the morning report | Alpine container images have no time zone database |
| pytest, pytest-asyncio | 9.1, 1.4 | Tests | Async tests without boilerplate (`asyncio_mode = auto`) |

No agent framework (LangChain, Semantic Kernel, AutoGen, …): the tool-calling loop is
about 100 lines, and owning it keeps behaviour, security checks and tests in our hands.
Reasons: [architecture.md](architecture.md#why-our-own-loop-and-no-framework).

## The model: MS Foundry

| | Choice | Why |
|---|---|---|
| Service | **Microsoft Foundry** (Azure AI Services resource) | Models hosted in your own Azure tenant, with Entra ID access and EU data zones. Other OpenAI-compatible endpoints work experimentally; multi-provider support is on the [roadmap](roadmap.md) |
| Model | **gpt-5.4-mini**, version 2026-03-17 | A reasoning model with tool calling at a low price; gpt-4.1 is closed to new deployments |
| Deployment type | **DataZoneStandard** | Processing stays in the EU data zone; pay per token |
| Capacity | 200k tokens per minute | A full check's busiest minute is ~90k tokens |
| Upgrade policy | OnceCurrentVersionExpired | The tested version stays until its retirement date |
| API | **Chat Completions** (not the Responses API) | Supported by every chat model in Foundry, so switching models is a setting |
| Parameters | `reasoning_effort=medium`, no `temperature` | gpt-5.x reasons only when asked; reasoning models accept only the default temperature |
| Authentication | Entra ID, role *Cognitive Services OpenAI User* | No keys; Workload Identity in the cluster |
| Resources | One for non-prod, one for prod (e.g. `my-foundry-nonprod`, `my-foundry-prod`) | Dedicated resources: own quota, network rules, cost |

Details: [foundry-concepts.md](foundry-concepts.md), [foundry-setup.md](foundry-setup.md).

## Reading the cluster

| Component | Version | Role | Why |
|---|---|---|---|
| **kubernetes-mcp-server** (containers/kubernetes-mcp-server) | 0.0.67 | Preferred source of single objects, logs and metrics, as MCP tools | Runs in a cluster and speaks streamable HTTP; read-only mode and denied resources (Secrets); tool descriptions written for models. Microsoft's `aks-mcp` is stdio-only and not meant to run in a cluster |
| **MCP** (Model Context Protocol) | streamable HTTP, stateless | How the agent lists and calls the server's tools | A standard: a new server version brings new tools without code changes |
| **kubectl** | client 1.37 (cluster 1.33) | The fallback tool, and the summary tools' `get -o json` | Always available; guarded by an allow-list |
| **Kubernetes API** | 1.33 (AKS) | The source of truth | Read-only RBAC in the cluster |

Locally, the MCP server runs with `npx -y kubernetes-mcp-server@0.0.67`; in the cluster
it runs as a sidecar container on `localhost:8080`, from the upstream image `ghcr.io/containers/kubernetes-mcp-server`.

## Metrics, alerts and notifications

| Component | Used for |
|---|---|
| **Prometheus** (kube-prometheus-stack) | PromQL tools; the `ALERTS` history for "alerts that fired overnight" |
| **Alertmanager** | Alerts firing now |
| **MS Teams Workflows** (Power Automate) webhook | Posting cards; the old Office 365 connectors are retired |
| **Adaptive Cards** 1.4 | The card format; built for at most 27,000 bytes |

## Platform

| Component | Role |
|---|---|
| **Kubernetes** (tested on AKS) | Runs the agent: one Deployment, one replica, agent + MCP sidecar |
| **Helm** chart (OCI, `oci://ghcr.io/k8s-servant/charts/k8s-servant`) | Installation with `helm install`, or a Flux HelmRelease (`examples/flux-helmrelease.yaml`) |
| **Azure Workload Identity** (optional) | The pod's identity for Foundry and Key Vault, without secrets |
| **External Secrets Operator** + **Azure Key Vault** (optional) | The Teams webhook URL: synced from a Key Vault secret into the agent's own Kubernetes Secret |
| **GitHub Container Registry** (`ghcr.io/k8s-servant`) | The agent image and the Helm chart, signed with cosign (keyless), with SBOM and provenance |
| **GitHub Actions** + **release-please** | Tests, linting and security scans on every pull request; releases build, scan, sign and publish the image and the chart |
| **Docker** (`python:3.14-alpine`, multi-stage) | The image: the package plus kubectl v1.33 (checksum-verified), UID 1000, runs with a read-only root filesystem |
| PersistentVolume (1 Gi) | Memory (`/data`) |

## Repository tooling

| Tool | Used for |
|---|---|
| setuptools (`pyproject.toml`) | Packaging; the `k8s-servant` command |
| pytest | Unit tests (330+), including documentation checks (`tests/test_docs.py`) |
| `scripts/setup-foundry.sh` (bash + `az` CLI) | Creates the Foundry resource and model deployment |

## Alternatives that were considered

| Alternative | Why not (for now) |
|---|---|
| **HolmesGPT** (open source AI SRE, CNCF) | Covers much of the same ground. A small own agent was chosen to keep the security model, the memory and the Teams flow simple and under the project's control; a fair comparison is planned |
| An agent framework (LangChain, Semantic Kernel, …) | Hides the loop that most needs to be understood and tested; the loop is small |
| Microsoft `aks-mcp` | stdio only, documented as not for running inside a cluster |
| The Responses API | Not supported by every Foundry model |
| A database server (PostgreSQL, …) | One replica and small data: SQLite on a volume is enough |
| A CronJob instead of a long-running Deployment | Memory, the MCP sidecar and the morning-report schedule are simpler in one long-running process |
