# Teaching the agent: environment description and skills

The agent can see the cluster's state through its tools. It cannot see **why** things
are the way they are, **what is normal here**, or **how your team investigates**
problems. You teach it these with plain Markdown. No code changes are needed, which
is what makes the agent universal: another team adopts it by writing its own
knowledge.

How this knowledge travels from the files to the model (loading, the assembled system
prompt with a real example, learned notes, `load_skill` during a run):
[how-the-agent-learns.md](how-the-agent-learns.md). This page is about *writing* it.

## Three kinds of knowledge

| Kind | Answers the question | Example | Lives in |
|---|---|---|---|
| **Environment description** | "What is this platform?" Facts shared by every cluster of the platform that the API cannot show. | "The service-mesh servers (e.g. Consul) run on VMs outside the cluster; there are no mesh server pods." | Helm value `knowledge.environment` |
| **Cluster notes** | "What is special about *this* cluster?" | "This cluster is recreated every Sunday; findings around that time are expected." | Helm value `knowledge.clusterNotes` |
| **Skills** | "How do I investigate X?" Step-by-step read-only procedures. | `workload-troubleshooting`: what to check for Pending, CrashLoop or OOM pods | `SKILL.md` files (built in), or Helm value `knowledge.extraSkills` |

The environment description and cluster notes always go into the system prompt in
full. Skills work differently, as described next.

**An example** for a fictional platform is in
[`examples/knowledge-values.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/knowledge-values.yaml) (environment
description) and [`examples/values-azure.yaml`](https://github.com/k8s-servant/k8s-servant/blob/main/examples/values-azure.yaml) (cluster
notes). Copy them and describe your own platform.

**Built-in skills:** `cluster-health-check` (used by every scheduled check),
`workload-troubleshooting`, `node-troubleshooting`, `flux-gitops-status` and
`prometheus-queries`.

**Learned notes** are a fourth, separate kind of knowledge. The agent writes them
itself with `save_note`, they live in its memory, and they are not reviewed through
Git. Use them as a source: when a note states a lasting fact, move it into the
environment description or the cluster notes (reviewed, shared, versioned) and delete
it from `notes.md`. See [memory.md](memory.md#learned-notes).

## How skills are used: the catalog and `load_skill`

Only each skill's **name and description** go into the system prompt, as the skill
catalog:

```
## Skill catalog

- cluster-health-check: Use for a scheduled or on-demand overall health sweep ...
- flux-gitops-status: Use when checking whether Flux GitOps is healthy ...
- node-troubleshooting: Use when a node is NotReady, cordoned, under memory, disk ...
- workload-troubleshooting: Use when a specific pod or workload is unhealthy ...
```

The model reads the catalog, decides which skill fits the question, and calls the
`load_skill` tool to get the full procedure. Then it follows it. This keeps the prompt
small however many skills you add (HolmesGPT uses the same pattern). It also means
**the description decides whether a skill is ever used**, so write it carefully.

## The SKILL.md format

```markdown
---
name: workload-troubleshooting
description: Use when a specific pod or workload is unhealthy - Pending, CrashLoopBackOff, OOMKilled, ...
---
# Workload troubleshooting

## Rules
- Read-only: name the fix, never apply it.

## Steps
### 1. Find the owner and the scope
- ...

## Report
The root cause, the evidence, the suggested fix, and what is still unknown.
```

- The YAML **frontmatter** between the `---` lines has `name` and `description`. The
  description should be under 1024 characters.
- The **skill's identity is its folder name**, or its key under `extraSkills`. If the
  frontmatter `name` differs, a warning is logged and the folder name or key wins.
- The **body** is Markdown that the model reads after calling `load_skill`.
- Files saved on Windows (CRLF, BOM) are accepted. A skill without frontmatter still
  loads, but has no description, which triggers a warning. A skill with broken YAML
  keeps its body and loses its description.

## Where knowledge is loaded from

### 1. The knowledge folder (`KNOWLEDGE_DIR`)

The default is the chart's folder `charts/k8s-servant/knowledge/`, which holds the built-in
skills. Two layouts are understood and can be mixed:

```
<dir>/environment.md                    environment description
<dir>/cluster.md                        cluster notes
<dir>/skills/<name>/SKILL.md            one folder per skill (used in this repository)
<dir>/skill__<name>.md                  flat layout (used when mounted from a ConfigMap,
                                        because ConfigMap keys cannot contain "/")
```

### 2. Helm values files (`--values`)

The chart reads these keys, and so does the CLI:

```yaml
knowledge:
  # Platform facts, shared by all clusters (e.g. knowledge-values.yaml).
  environment: |
    ## Platform
    - Clusters are private; egress goes through a corporate firewall.
    - Example: the service-mesh (e.g. Consul) servers run on VMs outside the cluster.
      In the cluster there are only the injector, sidecars, gateways and a CNI DaemonSet.
  # Notes for one cluster (e.g. my-cluster-values.yaml).
  clusterNotes: |
    - This is the experimental cluster: it is recreated every Sunday and stopped on
      weekday evenings.
  # Site-specific skills: name -> full SKILL.md content.
  extraSkills:
    service-mesh-troubleshooting: |
      ---
      description: Use when a meshed service cannot reach another service ...
      ---
      # Service mesh troubleshooting
      ...
```

**Order and precedence.** The folder is loaded first. Then each `--values` file is
applied in order:
- a non-empty `environment` or `clusterNotes` replaces the earlier value;
- `extraSkills` add to the built-in skills, and replace one that has the same name.

Pass the same files in the same order to `helm install -f` (or the Flux HelmRelease's
`valuesFrom`),
so **the CLI on your laptop and the agent in the cluster see identical knowledge**.

## Writing a good environment description

Write down what an experienced colleague would tell a new SRE on day one. Concentrate
on what the API cannot show and what would otherwise look like a problem:

- **What is normal here:** maintenance and reboot windows, clusters that are stopped or
  recreated on a schedule, components that restart by design.
- **What is absent on purpose:** for example "no service-mesh server pods, the servers
  are external VMs" or "no log shipping in the test cluster".
- **Where things live:** namespaces of platform components, the URLs of Prometheus and
  Alertmanager, which namespaces are business vs system, how to tell what manages a
  resource (Flux vs pipelines).
- **Policies that cause symptoms:** quotas generated per namespace, admission policies
  that block images or root containers, a descheduler that evicts crashing pods (and so
  erases their restart counts and previous logs).
- **What it is not:** no secrets, passwords, tokens or internal credentials. The text
  is sent to the model with every request.

Keep it factual, in short bullet points grouped by component. Remove facts as soon as
they are no longer true. Stale knowledge misleads the agent more than missing knowledge.

## Writing a good skill

- **Description = when to use it.** Start with "Use when ..." and list the symptoms and
  keywords people actually ask about. The model chooses skills by description alone.
- **Read-only steps.** Every step reads something. Where something needs fixing, the
  skill says "name the fix for the administrators", never "do X".
- **Numbered steps, broad to narrow.** Tell the model what to look at *and what each
  result means* (for example "exit code 137 = killed").
- **Say what to report.** Root cause, evidence, suggested fix, unknowns.
- **Built-in skills stay generic.** Anything site-specific goes into the environment
  description or into an `extraSkills` skill. A test (`tests/test_builtin_skills.py`)
  rejects site names and write commands in the built-in skills.
- **Name tools generically** ("list pods", "describe the pod"). The model picks the
  MCP tool or kubectl itself.

## Testing a new skill or environment change locally

1. Check that it loads:

   ```bash
   k8s-servant --values my-values.yaml tools      # the skill appears under "Skills:" with its source
   k8s-servant --values my-values.yaml doctor     # "knowledge" line: environment yes/no, skill count
   ```

2. Ask a question that should trigger it, and watch the INFO log for the `load_skill`
   call:

   ```bash
   k8s-servant --values my-values.yaml ask "Why can't service A reach service B?" 2> trace.log
   grep load_skill trace.log
   ```

3. If the skill is not loaded, improve its description, not the question. Real users
   will not phrase questions to match a skill.
