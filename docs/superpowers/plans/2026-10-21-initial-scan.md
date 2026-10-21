# Initial Scan (`k8s-servant onboard`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A read-only scan that turns what the cluster shows into facts with evidence, asks a human about what it is unsure of, writes the draft Helm values files, and in the cluster reports "Since the last scan" in the morning report.

**Architecture:** A new package `k8s_servant/scan/` of plain functions over a `Fetch` callable (the pattern of `tools/cluster_tools.py`): inputs → facts (catalog + per-topic discovery) → confirmation → rendering. The CLI command `onboard` wires it to `kubectl_json`; the morning report calls a re-scan that compares facts with the last scan stored in memory's `meta` table. The model is used only for the optional overview paragraph.

**Tech Stack:** Python 3.11+, asyncio, PyYAML (already a dependency), httpx2 (already used by `prometheus_tool.py`), pytest with recorded JSON fixtures.

**Spec:** `docs/superpowers/specs/2026-10-21-initial-scan-design.md` (issue #35). Read it before any task.

## Global Constraints

- Read-only: only `kubectl get ... -o json`, `kubectl version -o json` and `kubectl get --raw <service proxy GET path>`, built by this package, never from model or human text. Never request Secrets.
- Every kubectl call goes through `tools.cluster_tools.kubectl_json` (no shell; `asyncio.create_subprocess_exec`).
- A failed or forbidden input becomes an entry in `ScanResult.failed`, never an exception out of `scan()`.
- No new RBAC: `charts/k8s-servant/templates/clusterrole.yaml` stays unchanged.
- Nothing reaches a file as a plain statement unless its certainty is `seen`, `confirmed` or `told`. `unsure` facts render only as `TODO(onboard): <question>` lines.
- Prompts go to stderr; stdout carries only the command's result (file paths, or `--show` output).
- Times are UTC. Format in headers: `%Y-%m-%d %H:%M UTC`.
- The new setting `SCAN_BEFORE_REPORT` (bool, default `true`) gets a comment in `config.py` and a row in `docs/configuration.md` (a test checks both ways).
- Public repo: fixtures and tests use placeholder names only (`my-cluster`, `example.com`, `00000000-0000-0000-0000-000000000000`). No real cluster data, even "anonymised".
- Every module starts with a docstring (what and why); Google-style docstrings; one test file per module; test doubles in `tests/fakes.py`.
- Before every push: `.venv/bin/pytest`, `.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, the denylist scan and gitleaks. Commits: `git -c user.email=andriktr@gmail.com commit`, Conventional Commit messages, no attribution lines. Never edit `CHANGELOG.md`.
- PR bodies follow `.github/pull_request_template.md`.

## Review Focus

1. **A bare cluster** (kind, no catalog components, no Prometheus): the draft still loads as YAML, empty sections are left out, and the blind-spot TODOs are present. Pinned in Task 7 (kind fixture) and Task 9.
2. **Text that breaks YAML** in a statement or a human note (`: `, `#`, a leading `-`, quotes, non-ASCII, an embedded newline): the values file still loads and round-trips through `load_knowledge`. Pinned in Task 9.
3. **EOF or a non-terminal stdin** during the questions (Ctrl-D, piped input): treated as "skip", never a hang or a traceback. Pinned in Task 8.
4. **A stored scan that is corrupt or from an older shape** in `meta`: treated as "no previous scan" (a new baseline); the morning report still goes out. Pinned in Task 11.
5. **A cluster name that is not a safe file name** (`Prod/AKS 1`): the file name is sanitised to `prod-aks-1.yaml`; `CLUSTER_NAME` keeps the original. Pinned in Task 9.

## Planning calls (where the spec left a choice)

- **Human notes are kept word for word** (collapsed to one line). The spec allows the model to tidy them; that would let the model change what the human said, which works against anti-goal 2. The model writes only the overview.
- **Part 1 ships no command.** The spec suggests a first PR that prints from `onboard --no-input`; here the command arrives complete in Part 2, so no release carries a command whose output changes in the next one.
- **The re-scan runs inside `build_morning_report`** (an injected `scanner`), not in `Scheduler`. Same moment ("before each morning report"), no scheduler change, and `k8s-servant report` gets it too.
- **Only the in-cluster re-scan stores scans.** `onboard` on a laptop reads memory (open findings) but never writes a scan, so a laptop run cannot reset the pod's baseline.
- **The catalog is a Python data table** (`catalog.py` holds only rows), not a YAML file: no package-data setup, and the rows are type-checked.
- **Setting name:** `SCAN_BEFORE_REPORT`. **Storage:** JSON in the `meta` table under `last_cluster_scan`, versioned.
- **Noise thresholds:** alert firing ≥ 50% of 7 days; an event reason ≥ 50 occurrences and ≥ 25% of warning events; ≥ 20 restarts while ready; a finding open > 3 days.

## File structure

| File | Responsibility |
|---|---|
| `k8s_servant/scan/__init__.py` | Package docstring; re-exports `scan`, `Fact`, `ScanResult` |
| `k8s_servant/scan/facts.py` | `Fact`, `ScanResult`, topics, section titles, default scopes, (de)serialisation |
| `k8s_servant/scan/inputs.py` | The input table, `gather_inputs()`, Prometheus/Alertmanager access (direct or API-server proxy) |
| `k8s_servant/scan/catalog.py` | The component catalog (data only) and `component_facts()` |
| `k8s_servant/scan/discover.py` | Per-topic discovery (platform, organisation, network, monitoring, noise) and `scan()` |
| `k8s_servant/scan/questions.py` | Representative question, confirmation of unsure facts, blind spots |
| `k8s_servant/scan/draft.py` | Markdown body, suggested settings, values/markdown/env files, writing with `--force`, overview |
| `k8s_servant/scan/drift.py` | Store/load the last scan, compare, `rescan()` |
| `tests/fixtures/scan/{aks,eks,gke,kind}/*.json` | Synthetic `kubectl ... -o json` outputs, one file per input |
| `tests/fakes.py` | Add `fixture_fetch()` |

## PRs

| Part | Branch | PR title | Tasks |
|---|---|---|---|
| 1 | `feat/onboard-discovery` (from `docs/onboard-design`, which carries the spec and this plan) | `feat(onboard): discover cluster facts with evidence (#35)` | 1–7 |
| 2 | `feat/onboard-command` | `feat(onboard): the onboard command writes the draft values files (#35)` | 8–10 |
| 3 | `feat/onboard-rescan` | `feat(onboard): re-scan before the morning report and show what changed (#35)` | 11–12 |
| 4 | `docs/onboard` | `docs: first run with k8s-servant onboard (Closes #35)` | 13 |

Each part branches from `main` after the previous PR is merged. Before reporting a PR as ready: read all three feedback places, resolve fixed threads, and confirm `mergeStateStatus` is CLEAN.

---

## Part 1: discovery

### Task 1: Facts and scan results

**Files:**
- Create: `k8s_servant/scan/__init__.py`, `k8s_servant/scan/facts.py`
- Test: `tests/test_scan_facts.py`

**Interfaces:**
- Produces:
  - `TOPICS = ("platform", "components", "mesh", "secrets", "monitoring", "network", "organisation", "noise")`
  - `SECTION_TITLES: dict[str, str]`, in TOPICS order: Platform, Components, Service mesh, Secret management, Monitoring, Network, Organisation, What is normal here
  - `DEFAULT_SCOPE: dict[str, str]`: `platform` → `"cluster"`, `noise` → `"cluster"`, every other topic → `"platform"`
  - `CERTAINTIES = ("seen", "unsure", "confirmed", "told")`
  - `@dataclass(frozen=True) class Fact`: `key: str`, `topic: str`, `statement: str`, `evidence: tuple[str, ...]`, `certainty: str`, `scope: str`, `inputs: tuple[str, ...]`, `question: str = ""`, `setting: tuple[str, str] | None = None`, `setting_reason: str = ""`; methods `to_dict() -> dict`, `classmethod from_dict(data: dict) -> Fact`
  - `def fact(key, topic, statement, evidence, inputs, *, unsure_question="", scope=None, setting=None, setting_reason="") -> Fact`: certainty `unsure` when `unsure_question` is set, else `seen`; scope defaults to `DEFAULT_SCOPE[topic]`
  - `@dataclass class ScanResult`: `scanned_at: datetime`, `cluster: str`, `facts: list[Fact]`, `failed: dict[str, str]`, `kubectl_ok: bool`; property `ok -> bool` (returns `kubectl_ok`)
  - `def apply_scope(facts: list[Fact], representative: bool) -> list[Fact]`: when not representative, every fact gets `scope="cluster"`; otherwise unchanged

- [ ] **Step 1: Write the failing tests**

```python
def test_fact_defaults_to_seen_and_the_topic_scope():
    f = fact("component/kyverno", "components", "Kyverno runs.", ("CRD group kyverno.io",), ("crds",))
    assert (f.certainty, f.scope) == ("seen", "platform")

def test_a_question_makes_a_fact_unsure():
    f = fact("nodepool/user", "platform", "Node pool user: 3 nodes", (), ("nodes",), unsure_question="Correct?")
    assert (f.certainty, f.scope, f.question) == ("unsure", "cluster", "Correct?")

def test_fact_round_trips_through_a_dict():
    f = fact("monitoring/prometheus-url", "monitoring", "s", ("e",), ("services",), scope="cluster",
             setting=("PROMETHEUS_URL", "http://p.monitoring.svc:9090"), setting_reason="r")
    assert Fact.from_dict(json.loads(json.dumps(f.to_dict()))) == f

def test_not_representative_puts_everything_in_the_cluster_scope():
    f = fact("component/kyverno", "components", "s", (), ("crds",))
    assert apply_scope([f], representative=False)[0].scope == "cluster"
    assert apply_scope([f], representative=True)[0].scope == "platform"

def test_section_titles_follow_the_topic_order():
    assert list(SECTION_TITLES) == list(TOPICS)
    assert SECTION_TITLES["noise"] == "What is normal here"
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_scan_facts.py -v`. Expected: FAIL (module not found).
- [ ] **Step 3:** Implement `facts.py` and the package `__init__.py` (re-export added in Task 7). `from_dict` turns lists back into tuples.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(onboard): facts with evidence, certainty and scope (#35)`.

### Task 2: Inputs, fixtures and alert access

**Files:**
- Create: `k8s_servant/scan/inputs.py`, `tests/fixtures/scan/kind/*.json` (minimal, extended in Task 7)
- Modify: `tests/fakes.py`
- Test: `tests/test_scan_inputs.py`

**Interfaces:**
- Consumes: `Fetch` from `tools/cluster_tools.py` (note: for `get --raw` paths the parsed JSON may be a list).
- Produces:
  - `INPUTS: tuple[tuple[str, list[str], str | None], ...]`: (name, kubectl args, CRD name it needs or None). Names and args:
    `version` `["version", "-o", "json"]`; `nodes`, `namespaces`, `customresourcedefinitions` (name `crds`), `ingressclasses`, `csidrivers`, `mutatingwebhookconfigurations` (name `mutatingwebhooks`), `validatingwebhookconfigurations` (name `validatingwebhooks`) as `["get", <resource>, "-o", "json"]`; `services`, `deployments`, `daemonsets`, `statefulsets`, `pods`, `networkpolicies`, `ingresses` as `["get", <resource>, "-A", "-o", "json"]`; `events` `["get", "events", "-A", "--field-selector", "type=Warning", "-o", "json"]`; CRD-dependent: `gateways` (`gateways.gateway.networking.k8s.io`, `-A`), `gatewayclasses` (`gatewayclasses.gateway.networking.k8s.io`), `calico-policies` (`globalnetworkpolicies.crd.projectcalico.org`), `cilium-policies` (`ciliumclusterwidenetworkpolicies.cilium.io`).
  - `@dataclass class Inputs`: `items: dict[str, list[dict]]`, `failed: dict[str, str]`; `get(name) -> list[dict]` (`[]` when missing), `ok(name) -> bool`, `crd_names() -> set[str]`, `crd_groups() -> set[str]`. `version` is stored as a one-item list.
  - `async def gather_inputs(fetch: Fetch) -> Inputs`: `crds` first, then all other inputs concurrently (`asyncio.gather(..., return_exceptions=True)`). A CRD-dependent input whose CRD is absent is skipped (neither item nor failure). When `crds` failed, CRD-dependent inputs are marked failed `"CRD list not readable"`. Failure reasons are one line: `f"{type(exc).__name__}: {exc}"` cut to 200 chars.
  - `PromQuery = Callable[[str], Awaitable[dict]]` (PromQL → Prometheus API body); `AlertsNow = Callable[[], Awaitable[list[dict]]]`
  - `def direct_prometheus(url: str, timeout: float, transport=None) -> PromQuery`, `def direct_alertmanager(url: str, timeout: float, transport=None) -> AlertsNow` (wraps `prometheus_tool.fetch_firing_alerts`)
  - `def proxied_prometheus(fetch: Fetch, namespace: str, service: str, port: int) -> PromQuery`: `["get", "--raw", f"/api/v1/namespaces/{ns}/services/{service}:{port}/proxy/api/v1/query?query={urllib.parse.quote(q, safe='')}"]`
  - `def proxied_alertmanager(fetch, namespace, service, port) -> AlertsNow`: path `.../proxy/api/v2/alerts?active=true&silenced=false&inhibited=false`
  - `ALERT_SHARE_QUERY = 'count_over_time((max by (alertname) (ALERTS{alertstate="firing"}))[7d:5m]) / 2016'`
  - `async def alert_shares(query: PromQuery) -> dict[str, float]`: alertname → share of the 7 days it fired (0..1); raises `ValueError` when the body's status is not `success`.
- In `tests/fakes.py`: `def fixture_fetch(flavor: str, calls: list | None = None, fail: dict[str, Exception] | None = None) -> Fetch`. It maps args to `tests/fixtures/scan/<flavor>/<name>.json`, where name is `version` for `version`, the input name for `get <resource>` (match against `INPUTS`), and `raw-prometheus` / `raw-alertmanager` for `get --raw` paths containing `/api/v1/query` / `/api/v2/alerts`. A missing file raises `RuntimeError("NotFound")`; `fail[name]` is raised instead when given. Records `args` in `calls`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_gather_reads_every_input_once():
    calls = []
    inputs = await gather_inputs(fixture_fetch("kind", calls))
    assert inputs.ok("nodes") and inputs.get("version")[0]["serverVersion"]["gitVersion"]
    assert len(calls) == len({tuple(c) for c in calls})

async def test_a_forbidden_input_is_a_failure_not_an_exception():
    inputs = await gather_inputs(fixture_fetch("kind", fail={"networkpolicies": RuntimeError("Forbidden")}))
    assert inputs.failed["networkpolicies"] == "RuntimeError: Forbidden"
    assert inputs.ok("nodes")

async def test_crd_dependent_inputs_are_skipped_when_the_crd_is_absent():
    calls = []
    inputs = await gather_inputs(fixture_fetch("kind", calls))
    assert not any("gateways.gateway.networking.k8s.io" in c for c in calls)
    assert "gateways" not in inputs.failed

async def test_crd_dependent_inputs_fail_when_the_crd_list_failed():
    inputs = await gather_inputs(fixture_fetch("kind", fail={"crds": RuntimeError("Forbidden")}))
    assert inputs.failed["gateways"] == "CRD list not readable"

async def test_no_input_reads_secrets():
    assert not any("secret" in " ".join(args).lower() for _, args, _ in INPUTS)

async def test_alert_shares_through_the_proxy_path():
    calls = []
    query = proxied_prometheus(fixture_fetch("aks", calls), "monitoring", "kps-prometheus", 9090)
    # aks/raw-prometheus.json: {"status":"success","data":{"result":[{"metric":{"alertname":"KubeCPUOvercommit"},"value":[0,"0.96"]}]}}
    assert await alert_shares(query) == {"KubeCPUOvercommit": 0.96}
    assert calls[0][:2] == ["get", "--raw"] and "/services/kps-prometheus:9090/proxy/api/v1/query?query=" in calls[0][2]

async def test_alert_shares_rejects_an_error_body():
    async def query(_):
        return {"status": "error", "error": "bad"}
    with pytest.raises(ValueError):
        await alert_shares(query)

async def test_direct_prometheus_uses_the_configured_url():
    seen = []
    transport = httpx2.MockTransport(lambda r: (seen.append(str(r.url)), httpx2.Response(200, json={"status": "success", "data": {"result": []}}))[1])
    assert await alert_shares(direct_prometheus("http://p:9090", 5, transport)) == {}
    assert seen[0].startswith("http://p:9090/api/v1/query?query=")
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_scan_inputs.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `inputs.py`, `fixture_fetch`, a minimal kind fixture (`version`, `nodes` with one control-plane node, `namespaces`, empty-item files for the other inputs) and `aks/raw-prometheus.json`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(onboard): read the scan inputs once, each judged on its own (#35)`.

### Task 3: The component catalog

**Files:**
- Create: `k8s_servant/scan/catalog.py`
- Test: `tests/test_scan_catalog.py`

**Interfaces:**
- Consumes: `Inputs`, `fact()`.
- Produces:
  - `@dataclass(frozen=True) class Component`: `key: str`, `name: str`, `topic: str` (one of components, mesh, secrets, monitoring, network), `role: str` (e.g. `"policy engine"`), `crd_groups: tuple[str, ...] = ()`, `workloads: tuple[str, ...] = ()` (regexes, full match on Deployment/DaemonSet/StatefulSet names), `webhooks: tuple[str, ...] = ()` (regexes on webhook configuration names), `namespaces: tuple[str, ...] = ()` (exact names)
  - `CATALOG: tuple[Component, ...]`: data only. At least these keys: flux, argo-cd, kyverno, gatekeeper, cert-manager, ingress-nginx, traefik, envoy-gateway, keda, cluster-autoscaler, karpenter, velero, reloader, descheduler, metrics-server, istio, linkerd, consul, cilium-mesh, istio-egress-gateway, external-secrets, secrets-store-csi, csi-provider-azure, csi-provider-aws, csi-provider-gcp, vault-csi-provider, sealed-secrets, vault-injector, prometheus-operator, prometheus, alertmanager, grafana, loki, log-shipper (Alloy, Promtail, Fluent Bit), opentelemetry-collector, calico, cilium (also GKE's `anetd`), azure-cni, aws-vpc-cni, kindnet, flannel
  - `CATALOG_INPUTS = ("crds", "deployments", "daemonsets", "statefulsets", "mutatingwebhooks", "validatingwebhooks", "namespaces")`
  - `def component_facts(inputs: Inputs) -> tuple[list[Fact], set[str]]`: facts keyed `component/<key>` with the component's topic, and the set of namespaces where a matched workload runs (Task 4 uses it to tell system namespaces apart).
- Match rules (from the spec):
  - running = a matching workload with ready > 0 (`status.readyReplicas`, DaemonSet `status.numberReady`);
  - has_api = a CRD whose `spec.group` is in `crd_groups`, or a matching webhook configuration;
  - **seen**: running and (has_api or the row declares no `crd_groups`/`webhooks`). Statement: `f"{name} ({role}) runs in namespace `{ns}`"` (several namespaces: comma-joined, sorted);
  - **unsure**: has_api but not running → question `f"{name} CRDs or webhooks are installed but no {name} controller is running. Left over from an uninstall?"`; running but the declared CRDs are missing → question `f"{name} runs but its CRDs are missing. Is it installed fully?"`; only a catalog namespace exists → question `f"Namespace `{ns}` exists but no {name} workload runs. Is {name} used here?"`;
  - evidence: at most 3 items, e.g. `"CRD group kyverno.io"`, `"Deployment kyverno/kyverno-admission-controller (3 ready)"`, `"MutatingWebhookConfiguration istio-sidecar-injector"`.

- [ ] **Step 1: Write the failing tests** (build `Inputs` by hand with tiny dicts; helper `_inputs(**items)` in the test file)

```python
def test_every_catalog_row_is_valid():
    keys = [c.key for c in CATALOG]
    assert len(keys) == len(set(keys))
    for c in CATALOG:
        assert c.topic in {"components", "mesh", "secrets", "monitoring", "network"}
        assert c.crd_groups or c.workloads or c.webhooks
        for pattern in c.workloads + c.webhooks:
            re.compile(pattern)

def test_crds_and_a_running_controller_are_seen():
    facts, namespaces = component_facts(_inputs(crds=[_crd("kyverno.io")], deployments=[_deploy("kyverno", "kyverno-admission-controller", ready=3)]))
    f = _by_key(facts, "component/kyverno")
    assert f.certainty == "seen" and "kyverno" in namespaces
    assert "Deployment kyverno/kyverno-admission-controller (3 ready)" in f.evidence

def test_crds_without_a_controller_are_asked_about():
    facts, _ = component_facts(_inputs(crds=[_crd("kyverno.io")]))
    assert _by_key(facts, "component/kyverno").question.endswith("Left over from an uninstall?")

def test_a_controller_with_zero_ready_replicas_is_not_running():
    facts, _ = component_facts(_inputs(crds=[_crd("kyverno.io")], deployments=[_deploy("kyverno", "kyverno-admission-controller", ready=0)]))
    assert _by_key(facts, "component/kyverno").certainty == "unsure"

def test_a_component_without_crds_is_seen_from_its_workload():
    facts, _ = component_facts(_inputs(daemonsets=[_daemonset("kube-system", "kindnet", ready=1)]))
    assert _by_key(facts, "component/kindnet").topic == "network"

def test_nothing_matched_gives_no_facts():
    assert component_facts(_inputs()) == ([], set())
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_scan_catalog.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `catalog.py`. Workload and webhook regexes come from the upstream charts' default names; keep each row on one line where it fits.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(onboard): a catalog of well-known components (#35)`.

### Task 4: Platform and organisation facts

**Files:**
- Create: `k8s_servant/scan/discover.py`
- Test: `tests/test_scan_discover.py`

**Interfaces:**
- Consumes: `Inputs`, `fact()`.
- Produces:
  - `def platform_facts(inputs: Inputs) -> list[Fact]`:
    - `platform/kubernetes-version`: `f"Kubernetes {gitVersion}"`, evidence `("kubectl version",)`, inputs `("version",)`;
    - `platform/cloud` (scope `platform`): from node labels and `spec.providerID`. Seen: `kubernetes.azure.com/cluster` label → `"Managed service: AKS (Azure)"`; `eks.amazonaws.com/nodegroup` or `eks.amazonaws.com/compute-type` → `"Managed service: EKS (AWS)"`; `cloud.google.com/gke-nodepool` → `"Managed service: GKE (Google Cloud)"`; providerID `kind://` → `"Local kind cluster"`. Only a providerID prefix `azure://`, `aws://`, `gce://` → unsure, question `"Nodes run on <cloud>, but not with the managed service's labels. Is this a self-managed cluster?"`. Nothing recognised → no fact;
    - `nodepool/<pool>` per pool: pool label in this order: `kubernetes.azure.com/agentpool`, `agentpool`, `eks.amazonaws.com/nodegroup`, `karpenter.sh/nodepool`, `cloud.google.com/gke-nodepool`; else `control-plane` for nodes with `node-role.kubernetes.io/control-plane`, else `workers`. Statement `f"Node pool `{pool}`: {n} nodes, {types}"` with `types` from `node.kubernetes.io/instance-type`, e.g. `"2× Standard_D4s_v5, 1× Standard_D8s_v5"` (most first); append `" (system pool)"` when `kubernetes.azure.com/mode == "system"`.
  - `def organisation_facts(inputs: Inputs, component_namespaces: set[str]) -> list[Fact]`:
    - `organisation/namespaces` (scope `cluster`): `f"{total} namespaces: {system} for the platform, {apps} for applications"`; platform namespaces = `kube-*`, `default`, and `component_namespaces`;
    - `organisation/namespace-labels` (topic default scope `platform`): label keys (without keys containing `kubernetes.io` or `k8s.io`) present on at least half of the application namespaces, only when there are at least 3: `"Application namespaces carry the labels `team`, `cost-center`"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_aks_node_pools_and_cloud():
    nodes = [_node("aks-system-1", {"kubernetes.azure.com/cluster": "x", "kubernetes.azure.com/agentpool": "system", "kubernetes.azure.com/mode": "system", "node.kubernetes.io/instance-type": "Standard_D4s_v5"}),
             *[_node(f"aks-user-{i}", {"kubernetes.azure.com/cluster": "x", "kubernetes.azure.com/agentpool": "user", "node.kubernetes.io/instance-type": "Standard_D8s_v5"}) for i in range(3)]]
    facts = {f.key: f for f in platform_facts(_inputs(nodes=nodes, version=[{"serverVersion": {"gitVersion": "v1.33.2"}}]))}
    assert facts["platform/kubernetes-version"].statement == "Kubernetes v1.33.2"
    assert facts["platform/cloud"].statement == "Managed service: AKS (Azure)"
    assert facts["nodepool/user"].statement == "Node pool `user`: 3 nodes, 3× Standard_D8s_v5"
    assert facts["nodepool/system"].statement.endswith("(system pool)")

def test_a_bare_provider_id_is_asked_about():
    facts = {f.key: f for f in platform_facts(_inputs(nodes=[_node("n1", {}, provider_id="aws:///eu-west-1a/i-0")]))}
    assert facts["platform/cloud"].certainty == "unsure"

def test_kind_nodes_fall_back_to_roles():
    nodes = [_node("lab-control-plane", {"node-role.kubernetes.io/control-plane": ""}, provider_id="kind://docker/lab/lab-control-plane")]
    facts = {f.key: f for f in platform_facts(_inputs(nodes=nodes))}
    assert "nodepool/control-plane" in facts and facts["platform/cloud"].statement == "Local kind cluster"

def test_namespace_labels_need_three_application_namespaces():
    ns = [_ns("kube-system"), _ns("shop", {"team": "a"}), _ns("pay", {"team": "b"})]
    assert not [f for f in organisation_facts(_inputs(namespaces=ns), set()) if f.key == "organisation/namespace-labels"]

def test_component_namespaces_count_as_platform():
    ns = [_ns("kube-system"), _ns("kyverno"), _ns("shop")]
    f = organisation_facts(_inputs(namespaces=ns), {"kyverno"})[0]
    assert f.statement == "3 namespaces: 2 for the platform, 1 for applications"
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_scan_discover.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement both functions in `discover.py`. Shared test builders (`_inputs`, `_node`, `_ns`, `_deploy`, `_crd`, ...) go in `tests/fakes.py` so Tasks 3–6 share them; move the Task 3 helpers there too.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(onboard): platform and namespace facts (#35)`.

### Task 5: Network facts

**Files:**
- Modify: `k8s_servant/scan/discover.py`
- Test: `tests/test_scan_discover.py`

**Interfaces:**
- Produces `def network_facts(inputs: Inputs) -> list[Fact]` (topic `network`):
  - `network/default-deny-ingress`: namespaces with a NetworkPolicy whose `podSelector` is empty, `Ingress` in `policyTypes` (or `policyTypes` missing) and no `ingress` rules. Seen: `f"Inbound pod traffic is denied by default in {n} of {m} application namespaces"` (application namespaces as in Task 4: not `kube-*`/`default`);
  - `network/default-deny-egress`: an empty-podSelector policy with `Egress` in `policyTypes`. With no rules: seen `f"Outbound pod traffic is denied by default in {n} namespaces"`. With rules: unsure, statement `f"Outbound pod traffic is denied by default in {n} namespaces, except {allowed}"`, question `f"I read this as: outbound traffic is denied by default except {allowed}. Correct?"`. `allowed` lists, in this order, `DNS` (a rule allowing port 53), the `ipBlock.cidr` values, and `namespaces labelled k=v`, comma-joined, at most 5 items, then `and more`;
  - `network/no-network-policies`: no NetworkPolicy at all. Seen `"No NetworkPolicies: pod traffic inside the cluster is not restricted"`, but unsure (question `"No NetworkPolicies, but Calico or Cilium policies may restrict traffic. Which rules apply?"`) when a Calico or Cilium policy input failed or has items;
  - `network/calico-policies`, `network/cilium-policies` when readable and non-empty: `f"{n} Calico global network policies"` (Cilium: `cluster-wide network policies`);
  - `network/lb/<ns>/<name>` (scope `cluster`) per Service of type LoadBalancer: `"LoadBalancer service `ns/name` is internal-only"` or `"... is public"`, evidence the annotation. Internal when any of: `service.beta.kubernetes.io/azure-load-balancer-internal: "true"`, `service.beta.kubernetes.io/aws-load-balancer-internal` truthy, `service.beta.kubernetes.io/aws-load-balancer-scheme: internal`, `networking.gke.io/load-balancer-type: Internal`, `cloud.google.com/load-balancer-type: Internal`;
  - `network/ingress-exposure`: when there is at least one LoadBalancer and all are internal: unsure, statement `"Inbound traffic reaches the cluster only through internal load balancers"`, question `"All load balancers are internal. Is ingress internal only?"`;
  - `network/ingress-classes`: `"Ingress classes: nginx (default), internal"`. Default comes from the annotation `ingressclass.kubernetes.io/is-default-class: "true"`;
  - `network/gateways`: `f"Gateway API: {n} gateways ({classes})"` when the gateways input has items.
  - Inputs per fact: `("networkpolicies", "namespaces")`, plus `"calico-policies"`/`"cilium-policies"` where used, `("services",)`, `("ingressclasses",)`, `("gateways", "gatewayclasses")`.

- [ ] **Step 1: Write the failing tests**

```python
def test_default_deny_egress_with_exceptions_is_asked():
    pol = _netpol("shop", pod_selector={}, types=["Egress"], egress=[{"ports": [{"port": 53, "protocol": "UDP"}]}, {"to": [{"ipBlock": {"cidr": "10.0.0.0/8"}}]}])
    f = _by_key(network_facts(_inputs(networkpolicies=[pol], namespaces=[_ns("shop")])), "network/default-deny-egress")
    assert f.certainty == "unsure"
    assert f.question == "I read this as: outbound traffic is denied by default except DNS, 10.0.0.0/8. Correct?"

def test_deny_all_egress_is_seen():
    pol = _netpol("shop", pod_selector={}, types=["Egress"], egress=[])
    assert _by_key(network_facts(_inputs(networkpolicies=[pol], namespaces=[_ns("shop")])), "network/default-deny-egress").certainty == "seen"

def test_no_policies_is_unsure_when_calico_could_not_be_read():
    inputs = _inputs(networkpolicies=[], failed={"calico-policies": "RuntimeError: Forbidden"})
    assert _by_key(network_facts(inputs), "network/no-network-policies").certainty == "unsure"

def test_internal_load_balancers_ask_about_ingress():
    svc = _service("ingress-nginx", "ingress-nginx-controller", "LoadBalancer", {"service.beta.kubernetes.io/azure-load-balancer-internal": "true"})
    facts = {f.key: f for f in network_facts(_inputs(services=[svc]))}
    assert facts["network/lb/ingress-nginx/ingress-nginx-controller"].statement.endswith("is internal-only")
    assert facts["network/ingress-exposure"].certainty == "unsure"

def test_a_public_load_balancer_needs_no_question():
    facts = {f.key for f in network_facts(_inputs(services=[_service("a", "b", "LoadBalancer", {})]))}
    assert "network/ingress-exposure" not in facts
```

- [ ] **Step 2:** Run the new tests. Expected: FAIL.
- [ ] **Step 3:** Implement `network_facts()`.
- [ ] **Step 4:** Run `.venv/bin/pytest tests/test_scan_discover.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(onboard): network facts, inbound and outbound (#35)`.

### Task 6: Monitoring and noise facts

**Files:**
- Modify: `k8s_servant/scan/discover.py`
- Test: `tests/test_scan_discover.py`

**Interfaces:**
- Consumes: `alert_shares`, `PromQuery`, `AlertsNow`, `cluster_tools._owner`, `cluster_tools._event_time` (import them; do not copy), `StoredFinding`.
- Produces:
  - `def find_service(services: list[dict], name_part: str, port: int) -> tuple[str, str, int] | None`: (namespace, name, port) of a Service whose name contains `name_part`, has that port, is not headless (`clusterIP != "None"`) and does not end in `-operated`. Prefer the shortest name.
  - `def monitoring_facts(inputs: Inputs, prometheus_checked: bool, alertmanager_checked: bool) -> list[Fact]`: `monitoring/prometheus-url` and `monitoring/alertmanager-url` (scope `cluster`), statement `f"Prometheus answers in the cluster at {url}"`, `url = f"http://{name}.{ns}.svc:{port}"`, `setting=("PROMETHEUS_URL", url)`, `setting_reason` `f"Prometheus service found in namespace {ns}; checked: it answered through the API server proxy"` or `"...; not checked"`. Same for Alertmanager (`9093`, `ALERTMANAGER_URL`). The catalog already covers which monitoring tools run.
  - Thresholds: `NOISY_ALERT_SHARE = 0.5`, `NOISY_EVENT_MIN = 50`, `NOISY_EVENT_SHARE = 0.25`, `NOISY_RESTARTS = 20`, `STALE_FINDING_DAYS = 3`, `LONG_FIRING = timedelta(hours=24)`.
  - `def noise_facts(inputs: Inputs, config: dict, shares: dict[str, float] | None, firing_now: list[dict] | None, findings: list[StoredFinding], now: datetime) -> list[Fact]` (topic `noise`, scope `cluster`, all `unsure`):
    - `noise/alert/<name>` for shares ≥ `NOISY_ALERT_SHARE`, not in `config["alerts_ignore_names"]`: statement `f"`{name}` fires most of the time ({pct}% of the last 7 days)"`, question `f"`{name}` fired {pct}% of the last 7 days. Is it expected here, so I stop reporting it?"`, `setting=("ALERTS_IGNORE_NAMES", name)`, inputs `("prometheus",)`;
    - when `shares is None` and `firing_now` is given: the same key for alerts firing now whose `startsAt` is older than `LONG_FIRING`, question `f"`{name}` has been firing since {date}. Is it expected here, so I stop reporting it?"`, inputs `("alertmanager",)`;
    - `noise/event/<reason>`: warning events summed by `count` per reason, at least `NOISY_EVENT_MIN` and at least `NOISY_EVENT_SHARE` of all, not in `config["events_exclude_reasons"]`: question `f"`{reason}` makes up {pct}% of all warning events. Hide it by default?"`, `setting=("EVENTS_EXCLUDE_REASONS", reason)`, inputs `("events",)`;
    - `noise/restarts/<ns>/<kind>/<name>`: a pod container with `restartCount` ≥ `NOISY_RESTARTS` that is ready now, one fact per owning workload: question `f"{kind} `{ns}/{name}` restarted {n} times but is running. Is that by design?"`, inputs `("pods",)`;
    - `noise/finding/<fingerprint>`: open findings older than `STALE_FINDING_DAYS`: question `f"\"{title}\" has been open for {days} days. Is it expected here?"`, inputs `("memory",)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_prometheus_url_is_built_from_the_service():
    svc = _service("monitoring", "kps-prometheus", "ClusterIP", {}, ports=[9090])
    operated = _service("monitoring", "prometheus-operated", "ClusterIP", {}, ports=[9090], cluster_ip="None")
    f = _by_key(monitoring_facts(_inputs(services=[operated, svc]), True, False), "monitoring/prometheus-url")
    assert f.setting == ("PROMETHEUS_URL", "http://kps-prometheus.monitoring.svc:9090")
    assert f.setting_reason.endswith("checked: it answered through the API server proxy")

def test_an_alert_firing_most_of_the_week_is_asked():
    config = configure_app({})
    facts = noise_facts(_inputs(), config, {"KubeCPUOvercommit": 0.96, "Watchdog": 1.0, "Rare": 0.1}, None, [], NOW)
    assert [f.key for f in facts] == ["noise/alert/KubeCPUOvercommit"]
    assert facts[0].question == "`KubeCPUOvercommit` fired 96% of the last 7 days. Is it expected here, so I stop reporting it?"

def test_without_history_long_firing_alerts_are_used():
    alerts = [{"labels": {"alertname": "Old"}, "startsAt": "2026-10-18T00:00:00Z"}, {"labels": {"alertname": "New"}, "startsAt": "2026-10-21T08:00:00Z"}]
    assert [f.key for f in noise_facts(_inputs(), configure_app({}), None, alerts, [], NOW)] == ["noise/alert/Old"]

def test_a_dominant_event_reason_is_asked_unless_already_hidden():
    events = [_event("shop", "BackOff", count=60), _event("shop", "PolicyViolation", count=900), _event("x", "Other", count=40)]
    assert [f.key for f in noise_facts(_inputs(events=events), configure_app({}), None, None, [], NOW)] == []  # BackOff 6%, PolicyViolation hidden by default
    events = [_event("shop", "BackOff", count=60), _event("x", "Other", count=40)]
    assert [f.key for f in noise_facts(_inputs(events=events), configure_app({}), None, None, [], NOW)] == ["noise/event/BackOff"]

def test_noise_is_cluster_scope_and_never_seen():
    findings = [StoredFinding("oom/x/Deployment/a", "warning", "a is OOM", "", NOW - timedelta(days=5), NOW, "open", None)]
    facts = noise_facts(_inputs(), configure_app({}), {}, None, findings, NOW)
    assert all(f.scope == "cluster" and f.certainty == "unsure" for f in facts) and facts
```

- [ ] **Step 2:** Run the new tests. Expected: FAIL.
- [ ] **Step 3:** Implement `find_service`, `monitoring_facts`, `noise_facts`. Check `StoredFinding`'s real field order in `findings.py` and adjust the test's constructor call to it.
- [ ] **Step 4:** Run `.venv/bin/pytest tests/test_scan_discover.py -v`. Expected: PASS.
- [ ] **Step 5:** Commit `feat(onboard): monitoring URLs and what is normal noise (#35)`.

### Task 7: `scan()`, the Markdown body, and four cluster fixtures

**Files:**
- Modify: `k8s_servant/scan/discover.py`, `k8s_servant/scan/__init__.py`
- Create: `k8s_servant/scan/draft.py` (Markdown body only; Task 9 adds the rest), `tests/fixtures/scan/{aks,eks,gke}/*.json`; extend `tests/fixtures/scan/kind/`
- Test: `tests/test_scan_discover.py`, `tests/test_scan_draft.py`

**Interfaces:**
- Consumes: everything from Tasks 1–6.
- Produces:
  - `async def scan(fetch: Fetch, config: dict, cluster: str, memory: Memory | None = None, clock: Callable[[], datetime] | None = None, transport=None) -> ScanResult`. Order: `gather_inputs`; components; platform, organisation, network; alert access (configured `prometheus_url` via `direct_prometheus` → on any exception, the discovered service via `proxied_prometheus` → else `failed["prometheus"] = "not reachable; noise comes from events and restarts only"`; the same for Alertmanager, named `alertmanager`); monitoring; noise (findings from `memory.open_findings()` when memory is given; otherwise none). Copy `inputs.failed` into `ScanResult.failed`. `kubectl_ok` is True when at least one kubectl input succeeded. Facts are sorted by (TOPICS index, key).
  - `prometheus_checked` passed to `monitoring_facts` is True only when the proxy query succeeded.
  - In `draft.py`: `def render_markdown(facts: list[Fact], scope: str, overview: str = "") -> str`: facts of that scope only; `## <section title>` per topic in TOPICS order, sections without facts left out; overview (when given) first under `## Overview`. A `seen`/`confirmed` fact is `- <statement> [<evidence 1>; <evidence 2>]` (at most 2 evidence items, brackets left out when there is none); `told` is `- <statement> (from the team)`; `unsure` is `- TODO(onboard): <question>`. Multi-line text is collapsed to one line.
- Fixtures (synthetic, placeholder names, only the fields discovery reads):
  - **aks**: AKS labels with pools `system` (system mode, 2 nodes) and `user` (3 nodes); Azure CNI (`azure-cns`); Kyverno, Flux, cert-manager, ingress-nginx behind an internal LB; kube-prometheus-stack in `monitoring` (`kps-prometheus:9090`, `kps-alertmanager:9093`); External Secrets; Secrets Store CSI with the Azure provider; Consul connect injector (webhook and Deployment); default-deny egress allowing DNS and `10.0.0.0/8` in 3 namespaces; 900 `PolicyViolation` events; `raw-prometheus.json` with `KubeCPUOvercommit` at 0.96.
  - **eks**: EKS nodegroup labels; `aws-node`; Karpenter; Argo CD; Gatekeeper; Sealed Secrets; Linkerd; one public LB and one with `aws-load-balancer-scheme: internal`; no NetworkPolicies.
  - **gke**: GKE nodepool labels; `anetd` (Cilium); Istio (CRDs, istiod, sidecar-injector webhook); Gateway API with 1 gateway; Secrets Store CSI with the GCP provider; `cilium-policies` forbidden.
  - **kind**: one control-plane node, `kindnet`, Kyverno CRDs left behind without a controller, no monitoring.

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(("flavor", "expected"), [
    ("aks", {"platform/cloud", "nodepool/system", "nodepool/user", "component/azure-cni", "component/kyverno", "component/flux",
             "component/cert-manager", "component/ingress-nginx", "component/consul", "component/external-secrets",
             "component/secrets-store-csi", "component/csi-provider-azure", "component/prometheus", "component/alertmanager",
             "network/default-deny-egress", "network/ingress-exposure", "monitoring/prometheus-url", "noise/alert/KubeCPUOvercommit"}),
    ("eks", {"platform/cloud", "component/aws-vpc-cni", "component/karpenter", "component/argo-cd", "component/gatekeeper",
             "component/sealed-secrets", "component/linkerd", "network/no-network-policies"}),
    ("gke", {"platform/cloud", "component/cilium", "component/istio", "component/csi-provider-gcp", "network/gateways"}),
    ("kind", {"platform/cloud", "nodepool/control-plane", "component/kindnet", "component/kyverno"}),
])
async def test_scan_finds_the_expected_facts(flavor, expected):
    result = await scan(fixture_fetch(flavor), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert result.ok and expected <= {f.key for f in result.facts}

async def test_scan_on_kind_marks_leftover_crds_unsure_and_alerts_not_reachable():
    result = await scan(fixture_fetch("kind"), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert _by_key(result.facts, "component/kyverno").certainty == "unsure"
    assert result.failed["prometheus"].startswith("not reachable")

async def test_scan_prefers_the_configured_prometheus_url():
    calls = []
    transport = httpx2.MockTransport(lambda r: httpx2.Response(200, json={"status": "success", "data": {"result": []}}))
    await scan(fixture_fetch("aks", calls), configure_app({"PROMETHEUS_URL": "http://p:9090"}), "my-cluster", clock=lambda: NOW, transport=transport)
    assert not any(c[:2] == ["get", "--raw"] and "api/v1/query" in c[2] for c in calls)

async def test_a_scan_with_every_kubectl_input_failing_is_not_ok():
    fail = {name: RuntimeError("Unable to connect") for name, _, _ in INPUTS}
    result = await scan(fixture_fetch("kind", fail=fail), configure_app({}), "my-cluster", clock=lambda: NOW)
    assert not result.ok
```

```python
# tests/test_scan_draft.py
def test_unsure_facts_render_only_as_todo():
    facts = [fact("network/x", "network", "Outbound is denied", ("NetworkPolicy shop/deny",), ("networkpolicies",), unsure_question="Correct?"),
             fact("component/kyverno", "components", "Kyverno (policy engine) runs in namespace `kyverno`", ("CRD group kyverno.io",), ("crds",))]
    text = render_markdown(facts, "platform")
    assert "- TODO(onboard): Correct?" in text and "Outbound is denied" not in text
    assert "## Components\n\n- Kyverno (policy engine) runs in namespace `kyverno` [CRD group kyverno.io]" in text
    assert text.index("## Components") < text.index("## Network")

async def test_no_fixture_draft_states_anything_without_evidence_or_a_human():
    for flavor in ("aks", "eks", "gke", "kind"):
        result = await scan(fixture_fetch(flavor), configure_app({}), "my-cluster", clock=lambda: NOW)
        for line in render_markdown(result.facts, "cluster").splitlines():
            if line.startswith("- "):
                assert line.startswith("- TODO(onboard): ") or line.endswith("]") or line.endswith("(from the team)"), line
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_scan_discover.py tests/test_scan_draft.py -v`. Expected: FAIL.
- [ ] **Step 3:** Write the fixtures, implement `scan()` and `render_markdown()`, and re-export `scan`, `Fact`, `ScanResult` from the package. Facts that carry no evidence (none should) fail the last test: give them evidence.
- [ ] **Step 4:** Run the full suite, `ruff check`, `ruff format --check`. Expected: all PASS.
- [ ] **Step 5:** Commit `feat(onboard): scan a cluster into facts, tested on AKS, EKS, GKE and kind (#35)`.
- [ ] **Step 6: Open PR 1.** Branch `feat/onboard-discovery` from `docs/onboard-design`. Run the denylist and gitleaks over the diff against `main` (fixtures included). Body from the template; say that no command uses the scan yet.

---

## Part 2: the `onboard` command

### Task 8: Questions

**Files:**
- Create: `k8s_servant/scan/questions.py`
- Test: `tests/test_scan_questions.py`

**Interfaces:**
- Consumes: `Fact`, `fact()`, `dataclasses.replace`.
- Produces:
  - `Ask = Callable[[str], str]`: shows a prompt and returns the answer line without the newline; `""` on EOF.
  - `def terminal_ask(prompt: str) -> str`: writes the prompt to stderr (flushed), reads `sys.stdin.readline()`; EOF → `""`.
  - `REPRESENTATIVE_QUESTION = "Is this cluster representative of your platform (its components, network design and monitoring are the same on the other clusters)? [y/n] "`
  - `def ask_representative(ask: Ask) -> bool`: `y`/`yes` (any case) → True, anything else → False.
  - `BLIND_SPOTS: tuple[Fact, ...]`: unsure facts with an empty statement, key `blindspot/<id>`, `inputs=()`, evidence `()`:
    `egress` (network, platform) "How does outbound traffic leave the cluster (firewall, proxy, NAT gateway), and which destinations are allowed?"; `private-endpoints` (network, platform) "Which services does the cluster reach through private endpoints or peered networks?"; `inbound` (network, platform) "What sits in front of the cluster for inbound traffic (WAF, application gateway, CDN)?"; `maintenance` (noise, platform) "When are the maintenance and node reboot windows?"; `schedule` (noise, cluster) "Is this cluster stopped or recreated on a schedule? When?"; `external` (components, platform) "Which components of the platform run outside the cluster (mesh servers, DNS, registries, databases)?"
  - `def confirm(facts: list[Fact], ask: Ask) -> list[Fact]`: `seen` facts pass unchanged. For an unsure fact with a statement, prompt `f"\n{statement}\n{question}\n[y]es / [n]o / [a]dd a note / [s]kip: "`; `y` → certainty `confirmed`; `n` → dropped; `a` → ask `"Your note: "`, a non-empty note replaces the statement and makes it `told` (an empty note: unchanged); anything else → unchanged. For a blind spot (empty statement), prompt `f"\n{question}\n(Enter to skip): "`; a non-empty answer becomes the statement with certainty `told`. Notes are collapsed to one line.

- [ ] **Step 1: Write the failing tests**

```python
def _asker(*answers):
    replies, prompts = list(answers), []
    def ask(prompt):
        prompts.append(prompt)
        return replies.pop(0) if replies else ""
    ask.prompts = prompts
    return ask

def test_answers_confirm_drop_note_and_skip():
    unsure = [fact(f"k/{i}", "network", f"s{i}", ("e",), ("x",), unsure_question="q") for i in range(4)]
    out = {f.key: f for f in confirm(unsure, _asker("y", "n", "a", "Only DNS is allowed.", "s"))}
    assert out["k/0"].certainty == "confirmed" and "k/1" not in out
    assert (out["k/2"].statement, out["k/2"].certainty) == ("Only DNS is allowed.", "told")
    assert out["k/3"].certainty == "unsure"

def test_a_blind_spot_answer_becomes_a_told_fact():
    out = confirm([BLIND_SPOTS[0]], _asker("All egress goes through a\nfirewall."))
    assert (out[0].statement, out[0].certainty) == ("All egress goes through a firewall.", "told")

def test_eof_skips_everything():
    facts = [fact("k/0", "network", "s", ("e",), ("x",), unsure_question="q"), *BLIND_SPOTS]
    assert confirm(facts, _asker()) == facts
    assert ask_representative(_asker()) is False

def test_terminal_ask_prompts_on_stderr(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert terminal_ask("Q? ") == ""
    out, err = capsys.readouterr()
    assert (out, err) == ("", "Q? ")

def test_seen_facts_are_never_asked_about():
    ask = _asker()
    confirm([fact("k", "network", "s", ("e",), ("x",))], ask)
    assert ask.prompts == []
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_scan_questions.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `questions.py`.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(onboard): ask about what the scan is unsure of (#35)`.

### Task 9: Draft files

**Files:**
- Modify: `k8s_servant/scan/draft.py`
- Test: `tests/test_scan_draft.py`

**Interfaces:**
- Consumes: `render_markdown`, `ScanResult`, `Fact`, `knowledge.load_knowledge`.
- Produces:
  - `@dataclass(frozen=True) class Setting`: `name: str`, `value: str`, `reason: str`, `todo: str = ""` (a `TODO(onboard)` question shown next to it)
  - `def suggested_settings(facts: list[Fact], config: dict, cluster: str) -> list[Setting]`, in this order:
    `CLUSTER_NAME` (reason `"Name shown in reports and Teams cards"`; when `cluster` was the placeholder `my-cluster`, todo `"Set the real cluster name."`); `PROMETHEUS_URL` and `ALERTMANAGER_URL` from facts with that setting (reason = `setting_reason`; an unsure fact gives its question as todo); `ALERTS_IGNORE_NAMES` = `config["alerts_ignore_names"]` plus names from `confirmed` noise facts, comma-joined, todo listing the still-unsure ones (`"Also ignore KubeCPUOvercommit? It fired 96% of the last 7 days."`, one per name joined by a space), only when any noise alert fact exists; `EVENTS_EXCLUDE_REASONS` the same way.
  - `def file_slug(cluster: str) -> str`: lowercase, runs of characters outside `[a-z0-9-]` → `-`, stripped of `-`; empty → `my-cluster`.
  - `def header(result: ScanResult) -> list[str]`: comment lines: `f"# Generated by `k8s-servant onboard` from cluster {cluster} on {time} UTC."`, `"# Review before use: check every line, answer each TODO(onboard), then commit."`, and when `result.failed`: `"# Not checked: " + "; ".join(f"{name} ({reason})")`.
  - `def values_files(result: ScanResult, facts: list[Fact], settings: list[Setting], representative: bool, overview: str = "") -> dict[str, str]`: `knowledge-values.yaml` (only when representative) with `knowledge.environment`; `f"{file_slug(cluster)}.yaml"` with `knowledge.clusterNotes` and `appConfig`. Markdown goes in a `|` literal block indented 4 spaces; each setting is preceded by `# <reason>` and, when it has one, `# TODO(onboard): <todo>`; values are written with `json.dumps` (valid YAML strings). The overview goes in the environment when representative, otherwise in the cluster notes.
  - `def markdown_files(result, facts, settings, representative, overview="") -> dict[str, str]`: `environment.md` (only when representative), `cluster.md`, `settings.env` (`# <reason>` and `# TODO(onboard): ...` comments, then `NAME=<shlex.quote(value)>`). Markdown files start with the header as an HTML comment block (`<!-- ... -->`).
  - `def write_files(out_dir: Path, files: dict[str, str], force: bool) -> list[Path]`: creates `out_dir`; when not `force` and any target exists, raises `FileExistsError` naming all existing ones and writes nothing. Returns the paths written, in `files` order.

- [ ] **Step 1: Write the failing tests**

```python
async def test_values_files_load_back_as_knowledge(tmp_path):
    result = await scan(fixture_fetch("aks"), configure_app({}), "my-cluster", clock=lambda: NOW)
    facts = apply_scope(result.facts, representative=True)
    files = values_files(result, facts, suggested_settings(facts, configure_app({}), "my-cluster"), True)
    paths = write_files(tmp_path, files, force=False)
    knowledge = load_knowledge(tmp_path / "empty", [str(p) for p in paths])
    assert knowledge.environment.strip() == render_markdown(facts, "platform").strip()
    assert yaml.safe_load(files["my-cluster.yaml"])["appConfig"]["PROMETHEUS_URL"] == "http://kps-prometheus.monitoring.svc:9090"

def test_hostile_text_survives_yaml(tmp_path):
    note = fact("blindspot/egress", "network", "Proxy: http://proxy.example.com:3128 # all \"egress\" - äöü", (), (), scope="cluster")
    note = replace(note, certainty="told")
    files = values_files(_result([note]), [note], [], False)
    assert "Proxy: http://proxy.example.com:3128 # all \"egress\" - äöü (from the team)" in yaml.safe_load(files["my-cluster.yaml"])["knowledge"]["clusterNotes"]

def test_not_representative_writes_only_the_cluster_file():
    assert set(values_files(_result([]), [], [], False)) == {"my-cluster.yaml"}

def test_unsure_noise_is_a_commented_todo_not_a_setting():
    noisy = fact("noise/alert/KubeCPUOvercommit", "noise", "s", ("e",), ("prometheus",), unsure_question="q", setting=("ALERTS_IGNORE_NAMES", "KubeCPUOvercommit"))
    s = {x.name: x for x in suggested_settings([noisy], configure_app({}), "my-cluster")}["ALERTS_IGNORE_NAMES"]
    assert s.value == "Watchdog,InfoInhibitor" and "KubeCPUOvercommit" in s.todo

def test_file_slug():
    assert file_slug("Prod/AKS 1") == "prod-aks-1" and file_slug("///") == "my-cluster"

def test_existing_files_are_not_overwritten_without_force(tmp_path):
    (tmp_path / "my-cluster.yaml").write_text("mine")
    with pytest.raises(FileExistsError, match="my-cluster.yaml"):
        write_files(tmp_path, {"knowledge-values.yaml": "a", "my-cluster.yaml": "b"}, force=False)
    assert not (tmp_path / "knowledge-values.yaml").exists()
    write_files(tmp_path, {"my-cluster.yaml": "b"}, force=True)
    assert (tmp_path / "my-cluster.yaml").read_text() == "b"

def test_markdown_format_and_settings_env(tmp_path):
    s = [Setting("PROMETHEUS_URL", "http://p.monitoring.svc:9090", "found", "")]
    files = markdown_files(_result([]), [], s, False)
    assert set(files) == {"cluster.md", "settings.env"}
    assert "# found\nPROMETHEUS_URL=http://p.monitoring.svc:9090" in files["settings.env"]
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_scan_draft.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement the functions. Check `load_knowledge`'s real signature in `knowledge.py` and adjust the first test to it.
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(onboard): write the draft as Helm values or Markdown files (#35)`.

### Task 10: Overview and the `onboard` command

**Files:**
- Modify: `k8s_servant/scan/draft.py`, `k8s_servant/cli.py` (module docstring, parser, `cmd_onboard`, `COMMANDS`)
- Test: `tests/test_scan_draft.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `scan`, `apply_scope`, `confirm`, `ask_representative`, `BLIND_SPOTS`, `terminal_ask`, the draft functions, `kubectl_json`, `missing_model_settings`, `build_chat`.
- Produces:
  - `OVERVIEW_PROMPT`: "You write the first lines of a Kubernetes platform description for the engineers who run it. In 2 to 4 short sentences of plain language, say what this platform is and what stands out. Use only the facts given; do not add anything, do not guess, no headings, no lists."
  - `async def write_overview(chat, facts: list[Fact]) -> str`: sends only facts with certainty `seen`, `confirmed` or `told`, one `- statement` per line; returns the stripped reply. Exceptions propagate.
  - Parser: `onboard` with `--out DIR` (default `k8s-servant-onboard`), `--format {values,markdown}` (default `values`), `--force`, `--no-input`, `--no-model`, `--cluster-name NAME`.
  - `async def cmd_onboard(args, config, knowledge, memory) -> int`:
    1. cluster = `--cluster-name`, else `config["cluster_name"]` unless it is `unknown-cluster`, else `my-cluster`;
    2. scan with `fetch = lambda a: kubectl_json(a, config["kubectl_path"], config["kubectl_timeout_seconds"])` and `memory`; not `ok` → stderr `f"Could not read the cluster: {first failure}"`, return 1;
    3. interactive = not `--no-input` and `sys.stdin.isatty()`; representative = `ask_representative(terminal_ask)` when interactive, else False; facts = `apply_scope(result.facts + list(BLIND_SPOTS), representative)` (blind spots keep their own scope only when representative); `confirm(facts, terminal_ask)` when interactive;
    4. overview unless `--no-model`; when model settings are missing, stderr `"No model configured: writing the draft without an overview."`; a model error → stderr with `model_error_text`, continue without an overview;
    5. build the files for `--format`, `write_files(Path(args.out), files, args.force)`; `FileExistsError` → stderr `f"{exc}. Use --force to overwrite."`, return 1;
    6. print each written path to stdout; stderr summary: `f"{n} facts, {todo} questions left open (TODO(onboard)). Review the files before use."`; return 0.
  - The existing-files check runs before the scan (build the target names from `--format`, the cluster slug and representative = True, so a later "no" never surprises): stop early with the same message.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_scan_draft.py
async def test_overview_gets_only_confirmed_facts():
    chat = ScriptedChat(answer("A small AKS platform."))
    facts = [fact("a", "platform", "Kubernetes v1.33.2", ("kubectl version",), ("version",)),
             fact("b", "network", "Hidden guess", ("e",), ("x",), unsure_question="q")]
    assert await write_overview(chat, facts) == "A small AKS platform."
    sent = chat.calls[0][0][-1].content  # check the real ScriptedChat call shape in tests/fakes.py
    assert "Kubernetes v1.33.2" in sent and "Hidden guess" not in sent
```

```python
# tests/test_cli.py (the clean_env fixture already fakes kubectl with an empty item list;
# these tests monkeypatch cli.scan to return a fixture scan instead)
def test_onboard_no_input_writes_the_cluster_file_only(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "aks")
    assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(tmp_path)]) == 0
    out, err = capsys.readouterr()
    assert out.strip() == str(tmp_path / "my-cluster.yaml")
    assert "TODO(onboard)" in (tmp_path / "my-cluster.yaml").read_text() and "questions left open" in err

def test_onboard_refuses_to_overwrite(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "aks")
    (tmp_path / "my-cluster.yaml").write_text("mine")
    assert cli.main(["onboard", "--no-input", "--no-model", "--out", str(tmp_path)]) == 1
    assert "--force" in capsys.readouterr().err and (tmp_path / "my-cluster.yaml").read_text() == "mine"

def test_onboard_markdown_format(monkeypatch, tmp_path):
    _fixture_scan(monkeypatch, "kind")
    assert cli.main(["onboard", "--no-input", "--no-model", "--format", "markdown", "--out", str(tmp_path)]) == 0
    assert {p.name for p in tmp_path.iterdir()} == {"cluster.md", "settings.env"}

def test_onboard_without_a_model_still_writes(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "kind")
    assert cli.main(["onboard", "--no-input", "--out", str(tmp_path)]) == 0
    assert "without an overview" in capsys.readouterr().err

def test_onboard_unreachable_cluster_exits_1(monkeypatch, tmp_path, capsys):
    _fixture_scan(monkeypatch, "kind", fail_all=True)
    assert cli.main(["onboard", "--no-input", "--out", str(tmp_path)]) == 1
    assert "Could not read the cluster" in capsys.readouterr().err
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_scan_draft.py tests/test_cli.py -v -k "overview or onboard"`. Expected: FAIL.
- [ ] **Step 3:** Implement `write_overview` and `cmd_onboard`; add the `onboard` line to the `cli.py` docstring. `_fixture_scan` (in `tests/test_cli.py`) patches `cli.scan` with a wrapper that calls the real `scan` with `fixture_fetch(flavor, fail=...)`.
- [ ] **Step 4:** Run the full suite and ruff. Expected: PASS.
- [ ] **Step 5:** Manual check against a throwaway kind cluster with its own kubeconfig (`kind create cluster --name onboard-test --kubeconfig <scratchpad>/kc`, then `KUBECONFIG=<scratchpad>/kc .venv/bin/k8s-servant onboard --no-model --out <scratchpad>/out`; answer a few questions; read the files; delete the cluster). Never use the default kubeconfig context or the `lab` cluster.
- [ ] **Step 6:** Commit `feat(onboard): the onboard command (#35)`, then open PR 2 (denylist, gitleaks, template).

---

## Part 3: re-scan in the cluster

### Task 11: Stored scans and the comparison

**Files:**
- Create: `k8s_servant/scan/drift.py`
- Test: `tests/test_scan_drift.py`

**Interfaces:**
- Consumes: `ScanResult`, `Fact.to_dict`/`from_dict`, `Memory.get_meta`/`set_meta`, `scan`.
- Produces:
  - `LAST_SCAN_KEY = "last_cluster_scan"`
  - `def save_scan(memory: Memory, result: ScanResult) -> None`: JSON `{"version": 1, "scanned_at": iso, "cluster": ..., "facts": [...], "failed": {...}}`.
  - `def load_scan(memory: Memory) -> ScanResult | None`: None when missing, not JSON, another `version`, or any fact fails `from_dict` (log a warning naming the problem, not the content).
  - `@dataclass class ScanChanges`: `baseline: bool = False`, `added: list[Fact]`, `removed: list[Fact]`, `changed: list[tuple[Fact, Fact]]` (old, new), `not_checked: dict[str, str]`, `error: str = ""`, `summary: str = ""` (baseline text); methods `headline() -> str` and `lines() -> list[str]`.
  - `def compare(old: ScanResult | None, new: ScanResult) -> tuple[ScanChanges, ScanResult]`: returns the changes and the result to store.
    - Noise facts (topic `noise`) are never compared; the stored result keeps the new noise facts.
    - A fact whose `inputs` include an input in `new.failed` is "untouched": the old version (if any) is kept in the stored result and nothing is reported; the new version is dropped.
    - `old is None` → `baseline=True`, `summary = f"First scan recorded: {c} components, {p} node pools"` (`c` = keys starting with `component/`, `p` = `nodepool/`).
    - Otherwise added / removed / changed (same key, different statement), each sorted by key.
  - `headline()`: `"failed: <error>"`, the summary on a baseline, `"no changes"`, or `f"{a} added, {r} removed, {c} changed"`.
  - `lines()`: added → `f"Added: {s} — add to knowledge.{target}: `- {s}`"` (`target` = `environment` for scope platform, else `clusterNotes`), plus `f" and appConfig.{name}: {value}"` when the fact suggests a setting; removed → `f"Gone: {s} — remove it from knowledge.{target}"`; changed → `f"Changed: {old} → {new}"`; finally, when `not_checked`: `"Not checked this time: " + ", ".join(sorted(not_checked))`.
  - `async def rescan(fetch: Fetch, config: dict, memory: Memory, clock=None) -> ScanChanges`: `scan(..., cluster=config["cluster_name"], memory=memory)`; not ok → `ScanChanges(error=first failure)` and memory unchanged; otherwise compare with `load_scan(memory)`, `save_scan` the merged result, return the changes.

- [ ] **Step 1: Write the failing tests**

```python
def _r(facts, failed=None):
    return ScanResult(NOW, "my-cluster", facts, failed or {}, True)

def test_first_scan_is_a_baseline():
    changes, stored = compare(None, _r([_f("component/kyverno"), _f("nodepool/user")]))
    assert changes.baseline and changes.headline() == "First scan recorded: 1 components, 1 node pools"

def test_added_removed_changed():
    old = _r([_f("component/kyverno"), _f("nodepool/user", "3 nodes")])
    new = _r([_f("component/flux"), _f("nodepool/user", "6 nodes")])
    changes, _ = compare(old, new)
    assert [f.key for f in changes.added] == ["component/flux"]
    assert [f.key for f in changes.removed] == ["component/kyverno"]
    assert changes.lines()[-1] == "Changed: 3 nodes → 6 nodes"

def test_facts_of_a_failed_input_are_kept_and_never_removed():
    old = _r([_f("network/calico-policies", inputs=("calico-policies",))])
    changes, stored = compare(old, _r([], failed={"calico-policies": "RuntimeError: Forbidden"}))
    assert not changes.removed and [f.key for f in stored.facts] == ["network/calico-policies"]
    assert changes.lines() == ["Not checked this time: calico-policies"]

def test_noise_is_not_compared():
    changes, _ = compare(_r([_f("noise/alert/A", topic="noise")]), _r([_f("noise/alert/B", topic="noise")]))
    assert changes.headline() == "no changes"

def test_corrupt_or_old_stored_scans_are_ignored(tmp_path):
    memory = Memory(tmp_path)
    for value in ("not json", json.dumps({"version": 0}), json.dumps({"version": 1, "facts": [{"key": 1}]})):
        memory.set_meta(LAST_SCAN_KEY, value)
        assert load_scan(memory) is None

async def test_a_failed_rescan_leaves_memory_unchanged(tmp_path):
    memory = Memory(tmp_path)
    memory.set_meta(LAST_SCAN_KEY, "keep me")
    fail = {name: RuntimeError("Unable to connect") for name, _, _ in INPUTS}
    changes = await rescan(fixture_fetch("kind", fail=fail), configure_app({}), memory, clock=lambda: NOW)
    assert changes.error and memory.get_meta(LAST_SCAN_KEY) == "keep me"

async def test_rescan_twice_reports_no_changes(tmp_path):
    memory = Memory(tmp_path)
    assert (await rescan(fixture_fetch("aks"), configure_app({}), memory, clock=lambda: NOW)).baseline
    assert (await rescan(fixture_fetch("aks"), configure_app({}), memory, clock=lambda: NOW)).headline() == "no changes"
```

- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_scan_drift.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `drift.py` (`_f` builds a seen fact in the test file).
- [ ] **Step 4:** Run the tests. Expected: PASS.
- [ ] **Step 5:** Commit `feat(onboard): store scans and compare them per input (#35)`.

### Task 12: Setting, morning report, Teams card and `onboard --show`

**Files:**
- Modify: `k8s_servant/config.py`, `docs/configuration.md` (one row), `k8s_servant/report.py`, `k8s_servant/teams.py`, `k8s_servant/cli.py`, `tests/test_cli.py` (`ENV_VARS`)
- Test: `tests/test_config.py`, `tests/test_report.py`, `tests/test_teams.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `rescan`, `ScanChanges`, `load_scan`, `values_files`, `suggested_settings`.
- Produces:
  - Setting `SCAN_BEFORE_REPORT` → `config["scan_before_report"]` (`_bool`, default `True`), comment: re-scan the cluster before each morning report and report what changed; no model calls. Row in `docs/configuration.md` next to the morning report settings: `| `SCAN_BEFORE_REPORT` | `true` | Before each morning report, scan the cluster again (no model, no tokens) and add "Since the last scan" with what changed. See docs/onboarding.md. |` (the page arrives in Task 13; until then link `docs/reports.md`, and Task 13 updates it).
  - `build_morning_report(..., scanner: Callable[[], Awaitable[ScanChanges]] | None = None)`: runs `scanner()` first, before the fresh check; any exception → `ScanChanges(error=type(exc).__name__)` and a warning log. `MorningReport.scan_changes: ScanChanges | None = None`.
  - `MorningReport.facts()` adds `("Since the last scan", changes.headline())` when `scan_changes` is not None. `render_facts_md` adds the section `"Since the last scan"` with `changes.lines()`.
  - `morning_card` adds the same section (lines cut to 300 chars, at most the card limit).
  - `report_and_post` passes `scanner=lambda: rescan(fetch, config, memory)` when `config["scan_before_report"]`, with the same `kubectl_json` fetch as `cmd_onboard` (extract a helper `_kubectl_fetch(config)` in `cli.py`).
  - `onboard --show`: no questions, no model, no scan. `load_scan(memory)` is None → stderr `"No stored scan yet: the agent stores one before each morning report (SCAN_BEFORE_REPORT)."`, return 1. Otherwise print the cluster values file built from the stored scan (`values_files(..., representative=False)`) to stdout, return 0.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
def test_scan_before_report_defaults_on():
    assert configure_app({})["scan_before_report"] is True
    assert configure_app({"SCAN_BEFORE_REPORT": "false"})["scan_before_report"] is False

# tests/test_report.py
async def test_morning_report_includes_the_scan_changes(tmp_path):
    changes = ScanChanges(added=[_seen("component/flux", "Flux (GitOps) runs in namespace `flux-system`")], removed=[], changed=[], not_checked={})
    report = await build_morning_report(_session(ScriptedChat(answer(findings_answer("ok", [])), answer("Quiet."))), Memory(tmp_path),
                                        configure_app({"CLUSTER_NAME": "my-cluster"}), clock=lambda: MONDAY_0800_VILNIUS,
                                        scanner=_returns(changes))
    assert ("Since the last scan", "1 added, 0 removed, 0 changed") in report.facts()
    assert "## Since the last scan\n\n- Added: Flux (GitOps) runs in namespace `flux-system`" in report.report_md

async def test_a_failing_scanner_does_not_stop_the_report(tmp_path):
    async def boom():
        raise RuntimeError("x")
    report = await build_morning_report(..., scanner=boom)  # same setup as above
    assert ("Since the last scan", "failed: RuntimeError") in report.facts()

async def test_no_scanner_no_section(tmp_path):
    report = await build_morning_report(...)  # same setup, no scanner
    assert "Since the last scan" not in report.report_md

# tests/test_teams.py
def test_morning_card_shows_the_scan_changes():
    card = morning_card("my-cluster", _report(scan_changes=ScanChanges(baseline=True, summary="First scan recorded: 3 components, 2 node pools", added=[], removed=[], changed=[], not_checked={})))
    assert "First scan recorded: 3 components, 2 node pools" in json.dumps(card)

# tests/test_cli.py
def test_onboard_show_prints_the_stored_scan(monkeypatch, tmp_path, capsys):
    memory = Memory(tmp_path / "data")
    save_scan(memory, _aks_result())
    assert cli.main(["onboard", "--show"]) == 0
    assert yaml.safe_load(capsys.readouterr().out)["knowledge"]["clusterNotes"]

def test_onboard_show_without_a_stored_scan(capsys):
    assert cli.main(["onboard", "--show"]) == 1
    assert "No stored scan yet" in capsys.readouterr().err
```

- [ ] **Step 2:** Run the new tests. Expected: FAIL.
- [ ] **Step 3:** Implement; add `"SCAN_BEFORE_REPORT"` to `ENV_VARS` in `tests/test_cli.py`. Adjust the test setups to the real helpers in `tests/test_report.py` and `tests/test_teams.py` (copy the existing report tests' setup, don't invent new fakes).
- [ ] **Step 4:** Run the full suite, ruff, `helm lint charts/k8s-servant`. Expected: PASS; `git diff main -- charts/` is empty.
- [ ] **Step 5:** Commit `feat(onboard): re-scan before the morning report and show what changed (#35)`, then open PR 3.

---

## Part 4: docs

### Task 13: Documentation

**Files:**
- Create: `docs/onboarding.md`
- Modify: `README.md` (a short "First run" section after Quickstart), `docs/deployment.md` ("First run" before the install steps), `docs/cli.md` (the `onboard` command and its flags), `docs/configuration.md` (point the `SCAN_BEFORE_REPORT` row at `onboarding.md`), `docs/reports.md` ("Since the last scan"), `docs/knowledge-and-skills.md` (one line: start from `k8s-servant onboard`), `docs/getting-started.md` (one step), `docs/roadmap.md` (row 0c done), `zensical.toml` (nav: `onboarding.md` under Get started, after Getting started), `AGENTS.md` (the `onboard` command in the Commands block)
- Test: `tests/test_docs.py` (existing link, anchor and list tests cover the new page)

**`docs/onboarding.md` covers**, in this order: what the command does and never does (read-only, no Secrets, no files overwritten); a first run on a laptop (`k8s-servant onboard`, the representative question, one confirmation shown as the spec's example, the files it writes); reviewing the draft (TODO lines, evidence in brackets, "from the team"); the flags table (`--out`, `--format`, `--force`, `--no-input`, `--no-model`, `--cluster-name`, `--show`); what it discovers per topic and what it cannot see (the blind spots); alert history (configured URLs, else the API-server proxy, else events only); in the cluster (re-scan before the morning report, "Since the last scan", baseline, per-input comparison, `SCAN_BEFORE_REPORT`, `kubectl -n k8s-servant exec deploy/k8s-servant -c agent -- k8s-servant onboard --show`); adding a component to the catalog (one row, one test). Provider-neutral wording.

- [ ] **Step 1:** Write the pages. Lists need a blank line before them and 4-space nesting (the docs tests enforce both).
- [ ] **Step 2:** Run `.venv/bin/pytest tests/test_docs.py -v` and `zensical build --strict --clean`. Expected: PASS, no warnings.
- [ ] **Step 3:** Run `.venv/bin/k8s-servant onboard --help` and check every flag in `docs/cli.md` and `docs/onboarding.md` matches it.
- [ ] **Step 4:** Commit `docs: first run with k8s-servant onboard (#35)`, open PR 4 with `Closes #35`.
