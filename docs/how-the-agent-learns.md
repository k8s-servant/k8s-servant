# How the agent learns: from files to the model

The model behind the agent knows Kubernetes in general, but nothing about *your*
platform, and it remembers nothing between requests. Everything it knows about your
clusters reaches it as text, in every request. This page follows that text from the
files in Git to the model, step by step, with example content and sizes measured on a
test cluster.

How to *write* good knowledge and skills is in
[knowledge-and-skills.md](knowledge-and-skills.md); this page explains how it works.

## The picture

```
 Git (reviewed)                                   agent's memory (DATA_DIR)
 ─────────────                                    ─────────────────────────
 charts/k8s-servant/knowledge/skills/*/SKILL.md   notes.md   ◀── save_note (the model)
 knowledge-values.yaml   (your platform)        │
 my-cluster-values.yaml  (this cluster)         │
        │                                            │
        │  1. load once per process                  │  3. read for every run
        ▼                                            ▼
   Knowledge(environment, cluster_notes, skills) ──▶ 2. system prompt ──▶ model
                                       │                  (every model call)
                                       │                       │
                                       └── skill bodies ◀── 4. load_skill(name)
                                                              (on demand, as a tool)
```

Four steps: **load** the knowledge, **build** the system prompt, **add** the learned
notes, and **load skills on demand** during the run.

## Step 1: loading the knowledge

`knowledge.load_knowledge()` runs once when the process starts (a CLI command, or
`k8s-servant serve`).

**1a. The knowledge folder** (`KNOWLEDGE_DIR`, default: the chart's
`charts/k8s-servant/knowledge/`). Two layouts are understood and can be mixed:

```
<dir>/environment.md                environment description
<dir>/cluster.md                    cluster notes
<dir>/skills/<name>/SKILL.md        one folder per skill (this repository)
<dir>/skill__<name>.md              flat layout (the chart's ConfigMap mounted as files,
                                    because ConfigMap keys cannot contain "/")
```

In this repository the folder holds only the five built-in, generic skills.

**1b. Helm values files**, applied in order (`--values` on the CLI; `helm -f` or the
Flux HelmRelease's `valuesFrom` in the cluster). Only the `knowledge:` section is read:

```yaml
knowledge:
  environment: |      # replaces the environment description (if not empty)
    # Example platform
    ...
  clusterNotes: |     # replaces the cluster notes (if not empty)
    - dev also hosts the *-stage namespaces ...
  extraSkills:        # adds skills, or replaces a skill with the same name
    service-mesh-troubleshooting: |
      ---
      description: Use when ...
      ---
      ...
```

A typical setup passes the same two files to the CLI and to the cluster, in the same
order:

```
knowledge-values.yaml    the platform (e.g. 5,900 characters, 10 sections;
                         start from examples/knowledge-values.yaml)
my-cluster-values.yaml   notes for this cluster (e.g. 270 characters)
```

So **the laptop and the cluster see identical knowledge**.

**1c. Parsing skills.** Each `SKILL.md` is split into YAML frontmatter (`name`,
`description`) and the Markdown body:

- the skill's identity is its folder name (or its `extraSkills` key); a different
  `name` in the frontmatter is logged as a warning;
- Windows line endings and a BOM are accepted;
- broken YAML keeps the body and loses the description (warning); a skill without a
  description stays loadable but the model will hardly ever choose it.

The log shows the result:

```
INFO k8s_servant.knowledge: Knowledge: environment=yes, cluster notes=yes, skills=5
```

`k8s-servant tools` lists every skill with the file it came from; `k8s-servant doctor`
reports the same counts.

**When changes take effect.** Knowledge is read at process start. Locally that is every
command. In the cluster a change to the values is a Helm upgrade; the
chart restarts the pod when the knowledge changes (a checksum annotation), so the next run uses it.

## Step 2: building the system prompt

At the start of every run (a question, a health check, the morning report),
`agent.build_system_prompt()` assembles the system prompt in this order:

| # | Part | Source | Example size |
|---|---|---|---|
| 1 | **Rules**: read-only; evidence for every statement; copy names exactly; check the skill catalog first; use the environment description and notes; save lasting facts; work on your own, never end with "if you want…"; start health questions with the summary tools; UTC; answer format | `BASE_PROMPT` in `agent.py` | ~2,300 chars |
| 2 | **Tools available in this session** (and a note when MCP is down) | the tool registry | ~300 chars |
| 3 | **Environment description** | `knowledge.environment` | 5,900 chars |
| 4 | **Cluster notes** | `knowledge.clusterNotes` | 270 chars |
| 5 | **Learned notes** (step 3) | `notes.md` | 0–8,000 chars |
| 6 | **Skill catalog**: one line per skill, name and description only | the skills' frontmatter | ~1,500 chars |

Total in the measured example: **about 10,000 characters, roughly 2,500 tokens**. With the
tool definitions (another ~3,000 tokens), the first model call of a run was measured at
about **6,000 prompt tokens**. Every following call resends the system prompt, so
Foundry's prompt cache serves most of it (cached tokens are billed at a discount).

An excerpt (start of the environment description and the catalog):

```
You are an experienced site reliability engineer (SRE) acting as a read-only
cluster administrator for the Kubernetes cluster "my-cluster". ...

Tools available in this session: cluster_problems and warning_events (compact
summaries; start here for health questions), load_skill, kubectl (read-only),
Kubernetes MCP tools (preferred for reading the cluster), save_note, Prometheus/...

## Environment description

# Example platform

## Clusters
- Two clusters: dev (also hosts staging namespaces, *-stage) and prod. ...
## What is normal here (do not report it as an incident)
...
## Skill catalog

- cluster-health-check: Use for a scheduled or on-demand overall health sweep of the
  cluster - nodes, unhealthy pods and workloads, disruption budgets, quotas, ...
- flux-gitops-status: Use when checking whether Flux GitOps is healthy - ...
- node-troubleshooting: Use when a node is NotReady, cordoned, under memory, disk or ...
- prometheus-queries: Use when metrics can confirm or time a problem - ...
- workload-troubleshooting: Use when a specific pod or workload is unhealthy - Pending, ...
```

To see the full prompt for your own values:

```bash
.venv/bin/python -c "
from k8s_servant.config import DEFAULT_KNOWLEDGE_DIR
from k8s_servant.knowledge import load_knowledge
from k8s_servant.agent import build_system_prompt
k = load_knowledge(DEFAULT_KNOWLEDGE_DIR, ['examples/knowledge-values.yaml', 'examples/values-azure.yaml'])
print(build_system_prompt('my-cluster', k, {'mcp', 'kubectl', 'cluster', 'knowledge', 'memory'}))"
```

**Why the environment description is always in full** and skills are not: the
description says what is *normal* here ("the test cluster is stopped on weekday evenings",
"the descheduler evicts crash-looping pods after 5 minutes"). The model needs that
before it judges anything, in every run. Skills are procedures, needed only for some
tasks.

## Step 3: learned notes

Learned notes are the agent's own memory of lasting facts, kept in `DATA_DIR/notes.md`:

```
- 2026-10-12: CronJob db-backup in payments-dev fails on Sundays by design (source DB is stopped)
```

- **Written by the model** with the `save_note` tool when it learns something that
  will matter later. One sentence, at most 500 characters, no secrets. People can edit
  the file too.
- **Read at the start of every run** (unlike the environment description, which is
  read once per process), so a note saved in one run is used in the next.
- **Size limit.** Only the newest notes up to `NOTES_MAX_CHARS` (default 8,000) go into
  the prompt, cut at a line boundary, with "(older notes omitted)" in front.
- **Visible to people.** Every new note is logged at WARNING and listed in the next
  morning report, because a note changes what every future run treats as normal, and
  it could have been planted by text in a log line (prompt injection).
- **Promote lasting facts to Git.** A note that stays true belongs in the environment
  description or cluster notes, where it is reviewed. Then delete it from `notes.md`.

Details: [memory.md](memory.md#learned-notes).

## Step 4: loading skills on demand

The model sees only the catalog. When a task matches a skill, it calls the
`load_skill` tool, and the agent returns the skill's full Markdown body as the tool
result. From then on the procedure is part of the conversation.

What this looked like in a health check on a test cluster (from the log):

```
tool load_skill({"name":"cluster-health-check"})      -> 5112 chars    (the task said: follow it)
tool cluster_problems({})                             -> 787 chars     (step 1 of the skill)
tool warning_events({"since_minutes": 60})            -> 1251 chars
tool load_skill({"name":"workload-troubleshooting"})  -> 3525 chars    (azure-cns looked unhealthy)
tool pods_get(... azure-cns-x7k2p)                    ...
tool load_skill({"name":"flux-gitops-status"})        -> 2170 chars    (step 7: Flux exists)
tool resources_list(... GitRepository / Kustomization / HelmRelease)
```

- **The description decides whether a skill is used.** The model chooses by the
  catalog line alone, so it should start with "Use when ..." and name the symptoms
  people ask about.
- **An unknown name** returns `ERROR: no skill named 'x'. Available skills: ...`, and
  the model corrects itself.
- **Why not put every skill in the prompt?** It would grow with every skill and be
  resent on every call. The catalog keeps the prompt small however many skills
  there are; a skill costs tokens only in runs that need it.

## Which knowledge wins

| Situation | Result |
|---|---|
| A values file has `environment` / `clusterNotes` | it replaces the folder's (or an earlier file's) text; an empty value changes nothing |
| `extraSkills` has a new name | the skill is added |
| `extraSkills` has a built-in skill's name | it replaces the built-in skill |
| A learned note contradicts the environment description | both are in the prompt; the model sees the conflict. Fix the note or the description |

## How the pieces are meant to be used

| Kind | Who writes it | Reviewed | Scope | In the prompt |
|---|---|---|---|---|
| Rules (`BASE_PROMPT`) | developers | code review | every cluster, every site | always |
| Built-in skills | developers | code review, must stay generic (a test rejects site names) | every site | catalog always, body on demand |
| Environment description | platform team | Git (values) | all clusters of the platform | always |
| Cluster notes | platform team | Git (values) | one cluster | always |
| Extra skills | platform team | Git (values) | the platform | catalog always, body on demand |
| Learned notes | the model (and people) | **no**: shown in the morning report | one cluster | always (newest 8,000 chars) |

**The improvement loop:** the agent reports something that is normal here → add one
line to the environment description or cluster notes (or let it save a note, then
promote it) → the next run knows. No code change.

## What the agent does not learn

- It does not fine-tune or train the model; nothing about your cluster leaves the
  request it was sent with (Foundry does not train on your prompts).
- It does not learn from its findings: the findings history is used for
  new / still open / resolved, not as knowledge.
- It cannot see what nobody wrote down. If an important fact is only in people's
  heads, the agent will flag it as a problem until someone adds it.
