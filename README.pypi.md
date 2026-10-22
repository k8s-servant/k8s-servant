# k8s-servant

[![PyPI](https://img.shields.io/pypi/v/k8s-servant)](https://pypi.org/project/k8s-servant/)
[![Python](https://img.shields.io/pypi/pyversions/k8s-servant)](https://pypi.org/project/k8s-servant/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://github.com/k8s-servant/k8s-servant/blob/main/LICENSE)
![Status](https://img.shields.io/badge/Status-early_alpha-orange)
![Access](https://img.shields.io/badge/Cluster_access-read--only-success)
[![Docs](https://img.shields.io/badge/docs-k8s--servant.dev-blue)](https://k8s-servant.dev)

A **read-only AI SRE** for Kubernetes clusters. It looks at a cluster the way an
experienced cluster administrator would: it finds what is wrong, explains why with
evidence, says what to do about it, and remembers what it already told you. **It never
changes anything.** The language model is your choice: Microsoft Foundry, OpenAI,
Anthropic, a self-hosted model through Ollama, or any OpenAI-compatible server.

> **Early-stage project.** Expect bugs and rough edges, and breaking changes before
> `v1.0`. It is read-only, but verify its findings before you act on them.

## Install

You need Python 3.12 or newer and `kubectl`.

```bash
uv tool install k8s-servant            # or: pipx install k8s-servant
uvx k8s-servant onboard                # or run once without installing
```

For Microsoft Foundry with Entra ID sign-in, install the `azure` extra:
`uv tool install 'k8s-servant[azure]'`.

## First run

With `kubectl` pointing at a cluster, let the agent draft what it should know about
your platform. It scans the cluster once (read-only, never Secrets), asks a few
questions and writes Helm values files you review:

```bash
k8s-servant onboard
```

Then choose a model and ask a question:

```bash
export LLM_PROVIDER=ollama LLM_MODEL=qwen3        # or anthropic / openai with LLM_API_KEY_FILE
export CLUSTER_NAME=demo MCP_URL=                 # kubectl only; reads your current context
k8s-servant doctor
k8s-servant ask "Are there any unhealthy pods? Why?"
```

In the cluster it runs from the Helm chart, with scheduled health checks and a morning
report to an MS Teams channel.

## Links

- Documentation: <https://k8s-servant.dev>
- Getting started: <https://k8s-servant.dev/getting-started/>
- Choose a model provider: <https://k8s-servant.dev/model-providers/>
- Deploy to a cluster: <https://k8s-servant.dev/deployment/>
- Source and issues: <https://github.com/k8s-servant/k8s-servant>
- Release notes: <https://github.com/k8s-servant/k8s-servant/releases>
