# k8s-servant: from read-only watcher to trusted teammate (vision)

Status: approved by the maintainer · Date: 2026-10-17 · Steps: #27–#32

This is a direction document, not a build plan for one feature. It records why the
project exists, where it should go, and the order of the big steps. Each step becomes
its own backlog issue with its own design before anything is built.

## 1. Intent

The sentence we want people to say when they recommend k8s-servant:

> **"It's like having one more person on the team."**

The point is not that it spots problems; many tools do that. It **explains**, the way a
good colleague does: *what* is wrong, *why* (with evidence), and *how* to fix it, in
language the reader can act on. It shows up on its own (scheduled checks, the morning
report), remembers what it already said, works where the team already is (Teams, the
developer portal, the terminal), and earns the right to do more over time.

**Why this project exists.** It is driven by curiosity and by the wish to offer
something existing tools don't. A landscape review (October 2026: HolmesGPT, K8sGPT,
kagent, kubectl-ai, Robusta, Komodor, Azure SRE Agent, Datadog Bits, PagerDuty and
others) shows that the following are **not done well by any open-source tool** today,
and fit the "teammate" idea:

1. Two-way chat in **MS Teams** (open-source tools are Slack-first; Teams with approval
   buttons exists only in paid products).
2. Answers **scoped to the person asking** (the most requested missing feature in the
   open-source tools, for example kagent issue #1270 and HolmesGPT issue #2347).
3. Scheduled checks that **remember and skip unchanged state**, so they stay cheap
   (HolmesGPT's own docs say every scheduled check makes at least one model call).
   k8s-servant already does this, which makes it the strongest existing differentiator.
4. **Cost** that is visible and can be capped.
5. **Measured accuracy** on the team's own cluster, and honesty about missing evidence.
6. **Actions as approved, predefined runbooks** rather than free-form model commands.

## 2. Who it serves

- **Platform and SRE teams** run it and are its first users, on clusters of any size.
  They receive checks and reports, ask questions, and later delegate routine tasks.
- **Developers** come next. They ask "why is my deployment failing / my app crashing"
  through the internal developer portal (Backstage, Port, ...) or chat, instead of
  messaging the platform team, and see only their own workloads.

## 3. Goals

- **G1. A teammate in Teams.** People @mention the bot in a channel or chat, ask
  questions, and get evidence-based answers in the conversation. Health-check and
  morning-report cards become interactive (ask a follow-up, grade an answer, later
  approve an action). *First priority.*
- **G2. A visible track record (the scoreboard).** Every answer and finding can be
  graded from three sources:
  - the person who asked (did it solve my problem?);
  - the platform team (expert review, also of findings nobody asked about);
  - the bot itself (after a suggested fix, did the problem actually go away? The finding
    moved to "resolved", the pod stopped restarting, the node is Ready again).

  The bot shows its accuracy **per kind of task**, for example "crash diagnoses: 92%
  confirmed over the last 30 days".
- **G3. A trust ladder for actions.** Each kind of action has a level:
  1. explain only;
  2. propose a fix, which a named human approves in Teams before it runs;
  3. do it and report (only for the easiest tasks).

  A task type moves up only when the platform team promotes it and its score is above a
  threshold (around **90%**), and moves down automatically when the score drops or an
  action goes wrong. The first candidates are restarting a crashing pod (an easy,
  well-bounded task) and **rolling node restarts after patching** (high value; nothing
  in open source explains and health-checks this today).
- **G4. Developer self-service, scoped per person.** A developer sees answers only about
  what they themselves are allowed to see, from the portal or chat, phrased for
  someone who is not a Kubernetes expert.
- **G5. Cost you can see and cap.** Tokens and an estimated price per run, totals per
  day, and budgets that stop or downgrade work when they are reached.
- **G6. Make the existing strengths known.** Document and benchmark the memory and
  skip-unchanged behaviour (cost per cluster per month), the morning report, and the
  read-only guarantees.

## 4. Non-goals

- Competing on generic "ask your cluster" chat in the terminal. That space is saturated
  (kubectl-ai, K8sGPT, HolmesGPT, `az aks agent`).
- Competing on the number of integrations or toolsets (HolmesGPT, with Microsoft behind
  it, and kagent will win that).
- Becoming an agent framework or platform (kagent's niche).
- Fully autonomous self-healing of incidents (heavily funded vendors; and it would
  break the trust that being read-only builds).
- A hosted SaaS. It stays self-hosted, in the user's cluster.

## 5. Anti-goals: it fails, even if it technically works, when…

- **It is wrong too often**, or sounds confident without evidence. Inaccurate answers
  are the first reason people would uninstall it.
- **It is expensive**, or its cost is a surprise.
- **It is slow** enough that people stop asking.
- **It does something nobody approved**, or something nobody can trace afterwards.
- **It weakens the read-only promise for people who don't opt in.** Without explicit
  action configuration, it must behave exactly like today's read-only agent.

## 6. Constraints and principles

- **Read-only stays the default and the core.** Actions are an opt-in layer that is
  separate from the read-only agent:
  - a **separate identity** with its own, narrow RBAC, never the reader's identity;
  - **predefined, versioned runbooks** (steps, health gates, stop-on-regression), not
    commands the model makes up;
  - **every step audited**: who approved it, what ran, and the result.

  The read-only agent's RBAC rules (no Secrets, get/list/watch only) never loosen.
- **Evidence or silence.** Answers cite their evidence and say which sources were not
  available, instead of concluding anyway.
- **Scoped to the asker, once askers are not all trusted.** In the first Teams version
  only the platform team may ask (an allow-list of channels and people), and answers
  use the agent's own read-only identity, which can see the whole cluster. When
  developers get access (step 5), each answer is limited to what the asking person may
  see (for example through Kubernetes impersonation of the asker), and the broad read
  identity is used only for scheduled checks and platform-team questions.
- **Reachable only where the user opts in.** Two-way Teams needs Microsoft's cloud to
  reach the bot: an Azure Bot registration, an Entra app and a public HTTPS endpoint
  (ingress, TLS, request validation). That is accepted, but as an **opt-in component**:
  a default install stays outbound-only and works exactly like today.
- **Self-hosted and provider-neutral.** It runs in the user's infrastructure and works
  with any model provider (issue #2). No *core* feature may depend on a SaaS. A chat
  channel necessarily depends on its platform (Teams on Azure Bot Service), which is why
  chat channels are opt-in components.
- **Cost is capped before the audience grows.** Chat opens model calls to people, not
  just schedules, so a simple per-day budget ships together with two-way Teams.
- **Public repository rules** (CLAUDE.md) apply: no site-specific data; secrets only from
  files or Secrets.

## 7. Order of the big steps

The order is decided; how each step is built is not. Each step gets its own design and
issue.

| # | Step | Builds on | Notes |
|---|---|---|---|
| 0 | Multi-provider LLM (#2), provider-neutral docs (#11) | n/a | Already planned; prerequisite for "any model" |
| 1 (#27) | **Two-way Teams for the fleet** (G1): one bot for all clusters, @mention questions, "which cluster?" in the conversation, interactive cards, follow-ups; platform-team allow-list; per-day budget | an HTTP API in each cluster's agent; a central, opt-in bot component that routes questions to the right cluster's agent | First visible differentiator; also brings multi-cluster routing forward |
| 2 (#28) | **Scoreboard** (G2): grading buttons on cards, self-verification from memory, accuracy per task type, plus the offline evaluation set already on the roadmap | 1 (the buttons), memory | The evaluation set gates step 4: no action is promoted without measured accuracy |
| 3 (#29) | **Cost meter and budgets** (G5): full version (per-run and per-day reporting, downgrade to a cheaper model) | the simple cap from step 1 | Can run in parallel with 2 |
| 4 (#30) | **Runbooks with the trust ladder** (G3): first restarting a crashing pod, then rolling node restarts after patching | 1 (approval in Teams), 2 (scores and evaluation set) | A separate action component with its own identity and audit trail from day one |
| 5 (#31) | **Developer self-service** (G4): scoped answers, a Backstage plugin or API integration | the HTTP API, per-asker impersonation | Opens the project to a second audience |
| 6 (#32) | **Make strengths known** (G6) | n/a | Ongoing; benchmarks for the skip-unchanged cost model |

Slack, other notifiers (#3, #4), Key Vault references (#20) and the docs site (#5) stay
on their own tracks and don't block this order.

## 8. Decided by the maintainer

- The identity: "one more person on the team" (explain what, why and how to fix).
- Audiences: platform and SRE teams first, developers through the portal later.
- Two-way Teams comes first.
- **One Teams bot for the whole fleet**, with the cluster chosen in the conversation, not
  one bot per cluster.
- **Azure Bot Service and a public inbound endpoint are acceptable**, as an opt-in
  component.
- **In the first Teams version, people who may ask can see the whole cluster**; access is
  limited to the platform team. Per-person scoping comes with developer access.
- Trust is earned through a scoreboard; easy tasks are delegated gradually once accuracy
  reaches about 90%.
- Grading comes from the asker, the platform team and the bot's own verification.
- Actions start with the platform team and may widen later.
- What would make people uninstall it: cost, wrong answers, slowness.

## 9. Calls made by the writer (please check)

- The trust level is **per task type**, not global, and demotion is automatic.
- Actions use a **separate identity** and **predefined runbooks**, never free-form model
  commands.
- **The action layer is a separate component (its own image and chart part)**, so the
  read-only agent's image can never write. The guarantee is enforced by packaging, not
  only configuration. CLAUDE.md's "no code path may change cluster objects" keeps
  applying to the read-only agent unchanged; the action component gets its own rules
  when step 4 is designed.
- Per-asker scoping (step 5) uses **Kubernetes impersonation** of the asking user (or an
  equivalent forwarded identity). Granting `impersonate` will need an explicit exception
  to the chart's "RBAC stays get/list/watch" rule, decided in that step.
- Two-way Teams is built on a **small HTTP API** in each cluster's agent. The central bot
  is its first caller; the CLI and a portal plugin can reuse it later. Its
  authentication is designed in step 1.
- **Step 0 (#2, #11) is not a hard blocker for step 1.** #2 is still the next piece of
  work, as already agreed, but Teams can start against the current model client.
- **The offline evaluation set belongs to step 2** and gates step 4, not step 1: the first
  Teams users are the platform team, who can judge answers themselves.
- **A simple per-day budget cap ships with step 1**; the full cost meter stays step 3.
- No hosted SaaS for the core, ever. The project stays self-hosted.
- Once this is approved, `docs/roadmap.md` is rewritten to follow this order (done with this document).

## 10. Left to each step's own design

How the Teams bot is registered and hosted (Bot Framework vs. other options); the
grading UI and how scores are stored and windowed; the exact threshold rules; the
runbook format; which portal comes first; how cost is estimated per provider. Each of
these is decided in that step's issue, inside the principles above.
