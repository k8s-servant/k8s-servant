# Initial scan: `k8s-servant onboard` (issue #35)

Status: design, for review. Date: 2026-10-21.

## Intent

The biggest setup effort today is writing the environment description: what the
platform is, what is installed, what is normal here. A weak description means weaker
answers. The agent should do what a new colleague does in their first week: look
around the cluster (read-only), write down what it found, **ask about what it is not
sure of**, and hand over a draft a human reviews and commits.

The maintainer's idea, which shapes the design: it is a **CLI command that pre-creates
the files** that are later fed to the cluster, the same Helm values files a
configuration repository already holds. In the cluster, the same scan runs again on
its own and reports **what changed** in the morning report.

## Goals

1. `k8s-servant onboard` scans the cluster of the current kubeconfig context (or the
   pod's own cluster) read-only and writes a draft environment description, cluster
   notes and suggested settings as Helm values files.
2. It discovers what matters most to the maintainer:
   - **Installed components**: GitOps, policy engines, cert-manager, ingress and Gateway
     controllers, autoscalers, storage and backup, and anything else in the catalog.
   - **Service mesh**: Istio, Linkerd, Consul, Cilium service mesh.
   - **Secret management**: External Secrets, Secrets Store CSI driver and its providers,
     Sealed Secrets, Vault agent injector. It never reads a Secret.
   - **Monitoring**: Prometheus, Alertmanager, Grafana, Loki and similar. Where possible
     it suggests `PROMETHEUS_URL` and `ALERTMANAGER_URL` built from in-cluster
     services.
   - **Network, inbound and outbound**: NetworkPolicies, Calico and Cilium policies,
     egress gateways, ingress controllers and how they are exposed (internal or public
     load balancers), the CNI. What lies outside the cluster (firewalls, NSGs, proxies,
     private endpoints) cannot be seen; the scan **asks** about it.
   - **What is normal noise here**: always-firing alerts, recurring warning events,
     restarts by design, findings that stay open for days without anyone acting.
   - **Platform basics**: Kubernetes version, cloud and managed service, node pools with
     sizes and counts, and how namespaces are organised.
3. When the scan is unsure, it **asks the human to confirm**, with the option to add a
   note in their own words. Things it cannot see at all are asked as open questions.
4. In the cluster, a re-scan before each morning report compares facts with the last
   scan and adds a **"Since the last scan"** section to the report and its Teams card.

## Non-goals

- The pod never changes the knowledge on its own. Changes are reported and suggested;
  a human puts them into the configuration repository, as today.
- No merging into existing files. A second cluster's scan writes new files; the human
  merges what applies.
- No discovery outside the cluster (cloud APIs, firewalls). That would need cloud
  credentials; it is a separate roadmap idea (read-only cloud access).
- No new RBAC. The scan uses what the chart already grants. Where RBAC hides something
  (for example the contents of a mesh's custom resources), the scan relies on what is
  visible (CRDs, namespaces, workloads, webhooks) and says what it could not check.

## Anti-goals (what would make this a failure even if it works)

The maintainer named two:

1. **A messy, hard-to-read description.** The draft must be short, grouped by topic,
   one fact per bullet, in plain language, in the style of
   `docs/knowledge-and-skills.md` ("Writing a good environment description").
2. **Wrong assumptions.** Nothing goes into the draft as fact unless the cluster showed
   it (with evidence) or the human confirmed it. What is unsure is asked; what is
   unanswered stays a clearly marked open question, never a statement.

## Constraints

- **Read-only, no Secrets, no shell**, as everywhere (AGENTS.md). Discovery uses fixed
  kubectl commands through the existing `kubectl_json` runner (no model-chosen
  arguments) and the existing Prometheus/Alertmanager client.
- Works with the chart's current RBAC: the built-in `view` role plus the
  `cluster-reader` role (nodes, namespaces, CRDs, webhook configurations, a list of
  known CRD groups). A failed or forbidden read becomes a "could not check" note,
  never an error that stops the scan.
- **Discovery is deterministic Python.** The model is never needed to find or compare
  facts. Re-scans in the cluster cost no tokens.
- The draft contains only what came from the cluster or from the human. No Secret
  values, no data from outside.
- Times are UTC. Settings come from environment variables with a comment per key and a
  row in `docs/configuration.md`.
- Tests use recorded cluster JSON and the fakes in `tests/fakes.py`; no real cluster.

## How it works

### 1. Discovery: facts with evidence

A scan produces a list of **facts**. A fact has:

- a stable **key** (for comparing scans), for example `component/kyverno`,
  `nodepool/user`, `monitoring/prometheus`, `network/default-deny-egress`;
- a **topic** (platform, components, mesh, secrets, monitoring, network,
  organisation, noise);
- a short **statement** in plain language;
- its **evidence** (the objects that show it: a CRD name, a Deployment, a label);
- a **certainty**: `seen` (the evidence is unambiguous) or `unsure` (the scan has a
  reading but needs a human to confirm it), with the question to ask;
- a **scope**: `platform` (true for every cluster of the platform, goes into the
  environment description) or `cluster` (true for this cluster, goes into cluster
  notes). One scan sees one cluster, so the scope is decided like this (decided by the
  maintainer): the **first question** of an interactive run is "Is this cluster
  representative of your platform?". If yes, each fact takes its topic's default scope
  from the catalog (components, mesh, secret management, monitoring stack and network
  design: platform; version, node pools, counts, URLs and noise: cluster). If no, and
  always with `--no-input` and in the pod, every fact is `cluster` scope and the
  platform file is not written;
- optionally a **suggested setting** (`PROMETHEUS_URL=...`, alert names for
  `ALERTS_IGNORE_NAMES`, event reasons for `EVENTS_EXCLUDE_REASONS`).

Inputs, all read-only and fetched once per scan:

- `kubectl version`, nodes, namespaces, CRDs, services, deployments, daemonsets,
  statefulsets, NetworkPolicies, ingresses, ingress classes, Gateway API objects,
  mutating and validating webhook configurations, and warning events;
- Prometheus: alerts that fired over the last 7 days and how much of that time;
- Alertmanager: alerts firing now;
- where Prometheus and Alertmanager are reached from (my call): `PROMETHEUS_URL` and
  `ALERTMANAGER_URL` when set (always the case in the pod, which cannot use the
  API-server proxy). On a laptop where they are not set, the scan reaches the
  Prometheus and Alertmanager services it discovered through the API server's service
  proxy (`kubectl get --raw .../services/<name>:<port>/proxy/...`, a read-only GET
  with the user's own access). If neither works, noise comes from warning events and
  restarts only, and the draft says that alert history could not be read. A suggested
  `PROMETHEUS_URL` is marked as checked only when it answered through the proxy;
- the agent's memory: findings that stayed open for days.

**The component catalog is data, not code**: a table of well-known components with the
signals that identify them (CRD groups, namespaces, workload names or images, webhook
names) and the topic they belong to. Adding a component means adding a row and a test.
A component found by a strong signal (its CRDs **and** its controller running) is
`seen`; a weak or partial signal (CRDs left behind but no controller, a namespace name
only) is `unsure`.

Examples of what becomes `unsure` and is asked:

- a NetworkPolicy pattern that looks like default-deny egress with a few allowed
  destinations ("I read this as: outbound traffic is denied by default except DNS and
  `*.azurecr.io`. Correct?");
- an ingress controller behind a load balancer with an internal-only annotation
  ("Ingress is internal only?");
- an alert that fired most of the last 7 days ("`KubeCPUOvercommit` fired 96% of the
  week. Is it expected here, so I stop reporting it?");
- leftover CRDs without a running controller.

**Blind spots** are asked every time, because the cluster cannot show them: outbound
firewall or proxy and allowed destinations, private endpoints, maintenance and reboot
windows, clusters stopped or recreated on a schedule, components managed outside the
cluster. Each can be skipped.

### 2. Confirmation (CLI, interactive)

After the scan, in a terminal, the command walks through the `unsure` facts and the
blind-spot questions one at a time:

```
Found NetworkPolicy default-deny-egress in 14 namespaces, allowing only DNS and
*.azurecr.io. I read this as "outbound traffic is denied by default".
Correct? [y]es / [n]o / [a]dd a note / [s]kip
```

- **y** keeps the fact, **n** drops it, **a** keeps or replaces it with the human's own
  words, **s** leaves it as an open question.
- Answers to blind-spot questions become facts marked as **told by** the human.
- With `--no-input`, or when stdin is not a terminal, no questions are asked: unsure
  facts and blind spots are written as clearly marked open questions (see 4).
- The prompts go to stderr, so stdout stays clean (AGENTS.md).

### 3. Writing the draft

To keep the draft clear and free of invention (the two anti-goals):

- **The body is rendered deterministically from the confirmed facts**: fixed sections
  in a fixed order (Platform, Components, Service mesh, Secret management, Monitoring,
  Network, Organisation, What is normal here), one bullet per fact, short evidence in
  brackets, facts told by the human marked as such.
- **The model writes only a short overview** (a few sentences at the top) from the
  same facts, and may tidy the human's notes into one clean bullet each. Its prompt
  forbids anything not in the facts. `--no-model` skips it; the draft is complete
  without it.
- This is my call: it trades some prose quality for a guarantee that every statement
  in the body traces back to evidence or to the human.

### 4. Output files

By default, a new folder (`--out`, default `./k8s-servant-onboard/`):

- `knowledge-values.yaml`: `knowledge.environment`, the platform-scope facts (only when
  the cluster was confirmed as representative).
- `<cluster-name>.yaml`: `knowledge.clusterNotes` (cluster-scope facts and noise) and
  `appConfig` with the suggested settings (`CLUSTER_NAME`, `PROMETHEUS_URL`,
  `ALERTMANAGER_URL`, `ALERTS_IGNORE_NAMES`, `EVENTS_EXCLUDE_REASONS`), each with a
  comment saying why it is suggested.

Both start with a comment: generated by `k8s-servant onboard`, cluster, date (UTC),
"review before use". Markdown is written as YAML literal blocks so it stays readable.
Open questions appear as `TODO(onboard): <question>` lines inside the Markdown and as
comments next to suggested settings, so they are visible in review and easy to grep.

`--format markdown` writes `environment.md`, `cluster.md` (for `KNOWLEDGE_DIR`) and
`settings.env` instead. Existing files are never overwritten without `--force`.

In the Helm install both files apply completely: `knowledge.*` and `appConfig`. On a
laptop, `--values` loads the knowledge part only (as today; settings still come only
from environment variables, AGENTS.md), so the suggested settings are also written to
`settings.env` for `set -a; source settings.env; set +a`. My call: no change to how
settings are loaded.

### 5. In the cluster: re-scan and "Since the last scan"

- The facts of every scan (keys, statements, evidence, scope; not the human's answers)
  are stored in the agent's memory (SQLite, `DATA_DIR`).
- Before each morning report, the scheduler runs the same deterministic scan (no
  model, no questions) and compares facts by key: **added**, **removed**, **changed**
  (for example node pool `user` 3 → 6 nodes). Noise facts are left out of the
  comparison, because they change daily and would drown the real changes.
- The differences become a **"Since the last scan"** section in the morning report
  (and so in its overview and Teams card), each with a ready-to-paste suggestion for
  the values files where it applies. Nothing is changed automatically.
- The first scan in a new pod is stored as a baseline and reported once as "first scan
  recorded: N components, M node pools" rather than as N additions.
- Each input (CRDs, nodes, NetworkPolicies, Prometheus, ...) is judged on its own (my
  call). Facts from an input that failed this time are kept from the last scan and
  never reported as "removed"; facts from inputs that succeeded replace the old ones.
  An input that is forbidden every time (for example Calico or mesh custom resources,
  which the chart's RBAC does not cover) stays a "could not check" note and does not
  block the comparison. If the scan as a whole fails (the API is unreachable), memory
  is not changed, the same rule as for health checks.
- `kubectl exec ... k8s-servant onboard --show` prints the last stored scan and the
  suggested values without asking anything, so the draft can be taken from a running
  pod.
- A setting turns the re-scan off; it is on by default and costs no tokens.

## Decided by the maintainer

- CLI command that writes the files fed to the cluster later.
- Scope: one question up front ("Is this cluster representative of your platform?");
  yes uses the catalog's default scope per topic, no (and `--no-input`) puts
  everything in the cluster file.
- Focus: installed components, normal noise, network inbound and outbound (including
  what the cluster cannot show), service mesh, secret management, monitoring tools.
- Unsure points are **asked**, with confirm or add-a-note; not guessed.
- In-cluster parts (stored facts, re-scan, drift) in the same project, reported in the
  morning report, not a separate card.
- Failure modes to avoid: messy description, wrong assumptions.

## My calls (please check)

- Output as Helm values files by default, split into platform (`knowledge-values.yaml`)
  and cluster (`<cluster>.yaml`) scopes, with `--format markdown` as an alternative.
- The draft body is rendered from facts, not written by the model; the model writes
  only an overview and tidies notes.
- The re-scan runs before each morning report; noise is judged over 7 days of alert
  history; noise is excluded from the drift comparison.
- The component catalog is a data table, extended by adding rows.
- Alert history on a laptop comes from `PROMETHEUS_URL`/`ALERTMANAGER_URL` or, if unset,
  the API server's service proxy; without either, noise comes from events and restarts.
- Suggested settings apply through Helm; locally they are written to `settings.env`.
  Settings loading does not change.
- Stored facts are compared per input; a failing input keeps its old facts.
- Never overwrite files without `--force`; no automatic merge.

## Left to the builder

- Module layout and names (for example a `discovery` module of pure functions over a
  `Fetch` callable, like `tools/cluster_tools.py`), the exact fact keys, the storage
  shape in memory (a table or JSON in `meta`), the setting name for turning the
  re-scan off, the exact Prometheus query for "fired N% of the week".
- The initial catalog contents beyond the components named above.
- How to split the work into PRs. A natural order: discovery and facts with
  `onboard --no-input` printing; then confirmation, writing files and the overview;
  then stored facts, re-scan and the report section; then docs.

## Done when

- `k8s-servant onboard` produces the two values files from recorded cluster data for
  at least AKS, EKS, GKE and kind fixtures, with tests on discovery per topic, the
  catalog, unsure-fact questions, `--no-input` markers, `--force`, and both formats.
- No fact reaches the files as a statement without evidence or a human's
  confirmation (tested).
- The re-scan, the comparison and the report section are tested with injected clocks
  and recorded scans; a failed scan changes nothing in memory.
- Docs: a "First run" section in the README and the deployment guide, a page for the
  command, the new setting in `docs/configuration.md`, and the CLI reference.
