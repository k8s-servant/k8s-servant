---
title: k8s-servant
hide:
  - navigation
  - toc
---

![k8s-servant](images/logo-light.svg#only-light){ width="420" }
![k8s-servant](images/logo-dark.svg#only-dark){ width="420" }

A **read-only AI SRE** for Kubernetes clusters. It looks at a cluster the way an
experienced cluster administrator would: it finds what is wrong, explains why with
evidence, says what to do about it, and remembers what it already told you. **It never
changes anything.** You choose the model: Azure AI Foundry, OpenAI, Anthropic, a
self-hosted model through Ollama, or any OpenAI-compatible server. Results go to the
terminal and to an MS Teams channel.

> [!WARNING]
> **Early-stage project.** k8s-servant is young and has run on only a few clusters so
> far. Findings can be wrong or incomplete, and settings, Helm values and commands may
> change before `v1.0`. It cannot change your cluster, but **verify its findings before
> you act on them**, and please
> [report what breaks](https://github.com/k8s-servant/k8s-servant/issues/new/choose).

<div class="grid cards" markdown>

-   **Try it in 10 minutes**

    ---

    A throwaway kind cluster with something broken in it, a model of your choice,
    and your first questions.

    [Getting started](getting-started.md)

-   **Choose a model**

    ---

    One recipe per provider: settings for a laptop, a Helm values file, sign-in,
    and what `doctor` should print.

    [Model providers](model-providers.md)

-   **Run it in a cluster**

    ---

    One pod from the Helm chart, with scheduled health checks, a morning report
    and Teams cards.

    [Deploying to a cluster](deployment.md)

-   **Why it is safe**

    ---

    Read-only RBAC without Secrets, an allow-listed kubectl, no shell, and what
    data goes to the model.

    [Security model](security.md)

</div>

## What it does

- **Answers questions.** "Are there unhealthy pods?", "why is X pending?", "is Flux
  healthy?". It investigates with read-only tools and answers with evidence: object,
  field, event, time.
- **Watches.** Every hour it runs a health check and remembers the result, so it
  reports problems as **new**, **still open** or **resolved** instead of repeating
  them. When nothing changed, it does not even call the model.
- **Reports every morning.** On weekday mornings it posts how the cluster did since the
  previous report, what came and went during the night, and what needs attention today.
- **Learns your platform.** What is normal on your platform goes into an environment
  description and skills, plain Markdown delivered through Helm values
  ([teaching the agent](knowledge-and-skills.md)).

## What it never does

- It never creates, changes or deletes anything in the cluster. Asked to fix
  something, it describes the fix for you to apply.
- It never reads Secrets: its RBAC has no access to them, the kubectl guard refuses
  them and the MCP server is configured to deny them.
- It never runs a shell. kubectl runs with an allow-list of read-only verbs and flags.

How it works: [the flow from a question to an answer](how-it-works.md). The source,
issues and releases are on [GitHub](https://github.com/k8s-servant/k8s-servant).
