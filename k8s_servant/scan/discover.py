"""Platform, organisation, network, monitoring and noise facts: what the cluster runs on,
how it is split up, how traffic is kept in check, where monitoring lives and what is
normal background noise.

Why: the Kubernetes version, the managed service, the node pools and the namespace
layout are the first things an environment description states, and all of them can
be read off the nodes and namespaces without asking anyone. The same holds for
network policies, load balancers and ingress classes. A fact is only made
from an input that was actually read; a failed read never becomes a statement.

Noise facts are always questions: that an alert fires all week or a pod restarts
often is observed, but whether that is by design is only known to the people who run
the cluster.
"""

import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from k8s_servant.findings import StoredFinding
from k8s_servant.scan.catalog import CATALOG_INPUTS, running_namespaces
from k8s_servant.scan.facts import Fact, fact
from k8s_servant.scan.inputs import Inputs
from k8s_servant.tools.cluster_tools import _owner, _parse_time

_INSTANCE_TYPE = "node.kubernetes.io/instance-type"
_CONTROL_PLANE = "node-role.kubernetes.io/control-plane"

# Pool label, in order of preference.
_POOL_LABELS = (
    "kubernetes.azure.com/agentpool",
    "agentpool",
    "eks.amazonaws.com/nodegroup",
    "karpenter.sh/nodepool",
    "cloud.google.com/gke-nodepool",
)

# (label, managed service): a node carrying the label proves the service.
_MANAGED = (
    ("kubernetes.azure.com/cluster", "AKS (Azure)"),
    ("eks.amazonaws.com/nodegroup", "EKS (AWS)"),
    ("eks.amazonaws.com/compute-type", "EKS (AWS)"),
    ("cloud.google.com/gke-nodepool", "GKE (Google Cloud)"),
)

# providerID prefix -> cloud, for nodes without a managed service's labels.
_PROVIDER_PREFIXES = (("azure://", "Azure"), ("aws://", "AWS"), ("gce://", "Google Cloud"))

_MIN_LABELLED_NAMESPACES = 3

_FREE_TEXT_LIMIT = 100


def clean(text: object) -> str:
    """Free text from the cluster or memory, made safe to put inside a statement.

    Backticks and line breaks would let a label or an alert name break out of its
    Markdown quoting (and into the agent's prompt), so they are removed, and the
    text is capped at 100 characters.
    """
    text = " ".join(str(text).replace("`", "").split())
    return text if len(text) <= _FREE_TEXT_LIMIT else text[: _FREE_TEXT_LIMIT - 1] + "\u2026"


def _labels(item: dict) -> dict:
    return item.get("metadata", {}).get("labels") or {}


def _pool_of(node: dict) -> tuple[str, str]:
    """The pool a node belongs to, and the evidence label for it."""
    labels = _labels(node)
    for label in _POOL_LABELS:
        if label in labels:
            return labels[label], label
    if _CONTROL_PLANE in labels:
        return "control-plane", _CONTROL_PLANE
    return "workers", ""


def _cloud_fact(nodes: list[dict]) -> Fact | None:
    """The cloud the nodes run on: proven by a managed service's labels, otherwise asked about."""
    for label, service in _MANAGED:
        count = sum(1 for n in nodes if label in _labels(n))
        if count:
            return fact(
                "platform/cloud",
                "platform",
                f"Managed service: {service}",
                (f"{_count(count, 'node')} with label {label}",),
                ("nodes",),
                scope="platform",
            )
    provider_ids = [n.get("spec", {}).get("providerID") or "" for n in nodes]
    if any(p.startswith("kind://") for p in provider_ids):
        return fact(
            "platform/cloud",
            "platform",
            "Local kind cluster",
            ("nodes with providerID kind://",),
            ("nodes",),
            scope="platform",
        )
    for prefix, cloud in _PROVIDER_PREFIXES:
        count = sum(1 for p in provider_ids if p.startswith(prefix))
        if count:
            return fact(
                "platform/cloud",
                "platform",
                f"Nodes run on {cloud}",
                (f"{_count(count, 'node')} with providerID {prefix}",),
                ("nodes",),
                unsure_question=(
                    f"Nodes run on {cloud}, but not with the managed service's labels. Is this a self-managed cluster?"
                ),
                scope="platform",
            )
    return None


def _label_value(label: str, pool: str) -> str:
    """`=pool` for a pool label; the control-plane role label carries no value."""
    return "" if label == _CONTROL_PLANE else f"={clean(pool)}"


def _pool_fact(pool: str, members: list[tuple[dict, str]]) -> Fact:
    """One fact per pool name; the evidence lists each label that named the pool."""
    nodes = [n for n, _ in members]
    types = Counter(_labels(n).get(_INSTANCE_TYPE) for n in nodes)
    types.pop(None, None)
    listed = ", ".join(f"{count}\u00d7 {clean(name)}" for name, count in types.most_common())
    statement = f"Node pool `{clean(pool)}`: {_count(len(nodes), 'node')}"
    if listed:
        statement += f", {listed}"
    if any(_labels(n).get("kubernetes.azure.com/mode") == "system" for n in nodes):
        statement += " (system pool)"
    by_label = Counter(label for _, label in members)
    evidence = tuple(
        f"{_count(count, 'node')} with label {label}{_label_value(label, pool)}" if label else "kubectl get nodes"
        for label, count in by_label.items()
    )
    return fact(f"nodepool/{clean(pool)}", "platform", statement, evidence, ("nodes",))


def platform_facts(inputs: Inputs) -> list[Fact]:
    """Facts about the Kubernetes version, the cloud and the node pools."""
    facts: list[Fact] = []
    if inputs.ok("version"):
        version = (inputs.get("version") or [{}])[0].get("serverVersion", {}).get("gitVersion")
        if version:
            facts.append(
                fact(
                    "platform/kubernetes-version",
                    "platform",
                    f"Kubernetes {clean(version)}",
                    ("kubectl version",),
                    ("version",),
                )
            )
    if inputs.ok("nodes"):
        nodes = inputs.get("nodes")
        cloud = _cloud_fact(nodes)
        if cloud:
            facts.append(cloud)
        pools: dict[str, list[tuple[dict, str]]] = {}
        for n in nodes:
            pool, label = _pool_of(n)
            pools.setdefault(pool, []).append((n, label))
        facts.extend(_pool_fact(pool, members) for pool, members in pools.items())
    return facts


def platform_namespaces(inputs: Inputs, component_namespaces: set[str]) -> set[str]:
    """Namespaces of the platform: `kube-*`, `default` and those the catalog's components run in.

    Every other namespace counts as an application namespace, the same way for the
    organisation and the network facts.
    """
    names = {n.get("metadata", {}).get("name", "") for n in inputs.get("namespaces")} - {""}
    return {name for name in names if name.startswith("kube-") or name == "default" or name in component_namespaces}


def _with_catalog(*own: str) -> tuple[str, ...]:
    """Inputs of a fact that depends on what the catalog found running (its namespaces, a mesh)."""
    return tuple(dict.fromkeys((*own, *CATALOG_INPUTS)))


def organisation_facts(inputs: Inputs, component_namespaces: set[str]) -> list[Fact]:
    """Facts about how the namespaces are split between platform and applications."""
    if not inputs.ok("namespaces"):
        return []
    names = {n.get("metadata", {}).get("name", ""): n for n in inputs.get("namespaces")}
    names.pop("", None)
    platform = platform_namespaces(inputs, component_namespaces)
    apps = [n for name, n in names.items() if name not in platform]
    facts = [
        fact(
            "organisation/namespaces",
            "organisation",
            f"{len(names)} namespaces: {len(platform)} for the platform, {len(apps)} for applications",
            ("kubectl get namespaces",),
            _with_catalog("namespaces"),
            scope="cluster",
        )
    ]
    if len(apps) >= _MIN_LABELLED_NAMESPACES:
        counts = Counter(key for n in apps for key in _labels(n) if "kubernetes.io" not in key and "k8s.io" not in key)
        common = [
            key for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if count * 2 >= len(apps)
        ]
        if common:
            listed = ", ".join(f"`{clean(key)}`" for key in common)
            facts.append(
                fact(
                    "organisation/namespace-labels",
                    "organisation",
                    f"Application namespaces carry the labels {listed}",
                    ("kubectl get namespaces",),
                    _with_catalog("namespaces"),
                )
            )
    return facts


_MAX_LISTED = 5

# Annotation -> values that make a LoadBalancer Service internal-only.
_INTERNAL_LB = (
    ("service.beta.kubernetes.io/azure-load-balancer-internal", {"true"}),
    ("service.beta.kubernetes.io/aws-load-balancer-internal", {"true", "1", "yes"}),
    ("service.beta.kubernetes.io/aws-load-balancer-scheme", {"internal"}),
    ("networking.gke.io/load-balancer-type", {"internal"}),
    ("cloud.google.com/load-balancer-type", {"internal"}),
)


def _count(n: int, noun: str, plural: str = "") -> str:
    """`1 gateway`, `3 gateways`; pass `plural` for irregular nouns."""
    return f"{n} {noun if n == 1 else plural or noun + 's'}"


def _ref(item: dict) -> str:
    meta = item.get("metadata", {})
    return f"{meta['namespace']}/{meta.get('name', '')}" if meta.get("namespace") else meta.get("name", "")


def _evidence(kind: str, items: list[dict]) -> tuple[str, ...]:
    """`NetworkPolicy shop/default-deny` per item; long lists end with a count."""
    shown = tuple(f"{kind} {_ref(i)}" for i in items[:_MAX_LISTED])
    if len(items) > _MAX_LISTED:
        shown += (f"and {len(items) - _MAX_LISTED} more",)
    return shown


# CRDs of other policy engines: where any exists, "no NetworkPolicies" proves nothing.
_POLICY_CRDS = {
    "networkpolicies.crd.projectcalico.org",
    "globalnetworkpolicies.crd.projectcalico.org",
    "ciliumnetworkpolicies.cilium.io",
    "ciliumclusterwidenetworkpolicies.cilium.io",
    "adminnetworkpolicies.policy.networking.k8s.io",
    "baselineadminnetworkpolicies.policy.networking.k8s.io",
}

# CRDs of service mesh authorization: a mesh may restrict traffic without any NetworkPolicy.
_MESH_POLICY_CRDS = {
    "authorizationpolicies.security.istio.io",
    "authorizationpolicies.policy.linkerd.io",
    "servers.policy.linkerd.io",
}


def _covering(policies: list[dict], direction: str) -> dict[str, list[dict]]:
    """Per namespace, the empty-podSelector policies that cover `direction`.

    Kubernetes adds policies up, so a namespace is only denied by default when none
    of them allows everything; the caller judges that from these groups.
    """
    groups: dict[str, list[dict]] = {}
    for p in policies:
        spec = p.get("spec", {})
        # An absent policyTypes means Ingress.
        if not spec.get("podSelector") and direction in (spec.get("policyTypes") or ["Ingress"]):
            groups.setdefault(p["metadata"].get("namespace", ""), []).append(p)
    return groups


def _opens_everything(policy: dict, direction: str) -> bool:
    """A rule without peers and ports allows all traffic."""
    return any(not rule for rule in policy["spec"].get(direction.lower()) or [])


def _only_dns(rule: dict) -> bool:
    ports = rule.get("ports") or []
    return bool(ports) and all(str(port.get("port")) == "53" for port in ports)


def _egress_allowed(policies: list[dict]) -> tuple[list[str], bool]:
    """What the egress rules let through, and whether some destination cannot be named.

    A rule's peers and ports both apply, so a rule for port 53 only is DNS whoever
    the peer is; other peers are named when they are a CIDR block or labelled
    namespaces, and counted as "other destinations" otherwise.
    """
    items: list[str] = []
    other = False
    for rule in (r for p in policies for r in p["spec"].get("egress") or []):
        if _only_dns(rule):
            items.append("DNS")
            continue
        peers = rule.get("to") or []
        other = other or not peers
        for peer in peers:
            block = peer.get("ipBlock") or {}
            selector = peer.get("namespaceSelector") or {}
            labels = selector.get("matchLabels")
            if block.get("cidr"):
                items.append(block["cidr"])
                other = other or bool(block.get("except"))
            elif labels and not peer.get("podSelector") and not selector.get("matchExpressions"):
                items.append("namespaces labelled " + ",".join(f"{k}={v}" for k, v in labels.items()))
            else:
                other = True
    # DNS first, then CIDR blocks, then namespaces, each once.
    order = sorted(dict.fromkeys(items), key=lambda i: 0 if i == "DNS" else 2 if i.startswith("namespaces") else 1)
    return order, other


def _listed(items: list[str]) -> str:
    text = ", ".join(items[:_MAX_LISTED])
    return text + ", and more" if len(items) > _MAX_LISTED else text


def _share(n: int, m: int) -> str:
    """`1 of 3 application namespaces`, `all 3 ...`, or `the only ...`."""
    if n < m:
        return f"{n} of {_count(m, 'application namespace')}"
    return "the only application namespace" if m == 1 else f"all {m} application namespaces"


def _policy_facts(inputs: Inputs, component_namespaces: set[str], mesh_seen: bool) -> list[Fact]:
    """Default-deny and missing-policy facts from NetworkPolicies, Calico and Cilium.

    A running service mesh may enforce its own authorization policies, so with one
    the missing NetworkPolicies become a question too.
    """
    if not inputs.ok("networkpolicies"):
        return []
    policies = inputs.get("networkpolicies")
    facts: list[Fact] = []
    if not policies:
        # Calico, Cilium or a service mesh may restrict traffic where no NetworkPolicy exists.
        others = (
            bool(inputs.crd_names() & _POLICY_CRDS)
            or any(inputs.get(name) for name in ("calico-policies", "cilium-policies"))
            or any(name in inputs.failed for name in ("calico-policies", "cilium-policies", "crds"))
        )
        mesh = mesh_seen or bool(inputs.crd_names() & _MESH_POLICY_CRDS)
        question = ""
        if others:
            question = "No NetworkPolicies, but Calico or Cilium policies may restrict traffic. Which rules apply?"
        elif mesh:
            question = "No NetworkPolicies, but the service mesh may restrict traffic with its own policies. Which rules apply?"
        facts.append(
            fact(
                "network/no-network-policies",
                "network",
                "No NetworkPolicies: Kubernetes network policy does not restrict pod traffic",
                ("kubectl get networkpolicies -A",),
                _with_catalog("networkpolicies", "crds", "calico-policies", "cilium-policies"),
                unsure_question=question,
            )
        )
    for name, label, noun in (
        ("calico-policies", "Calico", "global network"),
        ("cilium-policies", "Cilium", "cluster-wide network"),
    ):
        items = inputs.get(name)
        if items:
            plural = f"{label} {noun} policies"
            facts.append(
                fact(
                    f"network/{name}",
                    "network",
                    _count(len(items), f"{label} {noun} policy", plural),
                    _evidence(f"{label} policy", items),
                    (name,),
                )
            )
    ingress = _covering(policies, "Ingress")
    if inputs.ok("namespaces"):
        apps = {_ref(n) for n in inputs.get("namespaces")} - {""} - platform_namespaces(inputs, component_namespaces)
        # Strict: another covering policy with rules, or allowing everything, opens the namespace.
        covered = {
            space: group
            for space, group in ingress.items()
            if space in apps and not any(p["spec"].get("ingress") for p in group)
        }
        if covered:
            facts.append(
                fact(
                    "network/default-deny-ingress",
                    "network",
                    f"Inbound pod traffic is denied by default in {_share(len(covered), len(apps))}",
                    _evidence("NetworkPolicy", [p for group in covered.values() for p in group]),
                    _with_catalog("networkpolicies", "namespaces"),
                )
            )
    egress = {
        space: group
        for space, group in _covering(policies, "Egress").items()
        if not any(_opens_everything(p, "Egress") for p in group)
    }
    if egress:
        base = f"Outbound pod traffic is denied by default in {_count(len(egress), 'namespace')}"
        per_space = {space: _egress_allowed(group) for space, group in egress.items()}
        distinct = {(tuple(items), other) for items, other in per_space.values()}
        question = ""
        if len(distinct) > 1:
            base += ", each with its own exceptions"
            question = (
                f"Outbound traffic is denied by default in {_count(len(egress), 'namespace')}, "
                "each with its own exceptions. Which destinations are allowed?"
            )
        elif distinct != {((), False)}:
            items, other = next(iter(distinct))
            allowed = _listed(list(items))
            if other and len(items) <= _MAX_LISTED:
                allowed = f"{allowed} and other destinations" if allowed else "other destinations"
            base += f", except {allowed}"
            question = f"I read this as: outbound traffic is denied by default except {allowed}. Correct?"
        facts.append(
            fact(
                "network/default-deny-egress",
                "network",
                base,
                _evidence("NetworkPolicy", [p for group in egress.values() for p in group]),
                ("networkpolicies",),
                unsure_question=question,
            )
        )
    return facts


def _is_internal(svc: dict) -> tuple[bool, str]:
    """Whether a LoadBalancer Service is internal-only, with the annotation that says so."""
    annotations = svc.get("metadata", {}).get("annotations") or {}
    for key, values in _INTERNAL_LB:
        if str(annotations.get(key, "")).lower() in values:
            return True, f"{key}: {annotations[key]}"
    return False, ""


def _exposure_facts(inputs: Inputs) -> list[Fact]:
    """Facts about LoadBalancer Services and what they imply for inbound traffic."""
    if not inputs.ok("services"):
        return []
    facts: list[Fact] = []
    balancers = [s for s in inputs.get("services") if s.get("spec", {}).get("type") == "LoadBalancer"]
    internal_count = 0
    for svc in balancers:
        internal, annotation = _is_internal(svc)
        internal_count += internal
        ref = _ref(svc)
        facts.append(
            fact(
                f"network/lb/{ref}",
                "network",
                f"LoadBalancer service `{ref}` {'is internal-only' if internal else 'has no internal-only annotation'}",
                (annotation,) if internal else (f"Service {ref} has type LoadBalancer without an internal annotation",),
                ("services",),
                scope="cluster",
            )
        )
    if balancers and internal_count == len(balancers):
        facts.append(
            fact(
                "network/ingress-exposure",
                "network",
                "Inbound traffic reaches the cluster only through internal load balancers",
                tuple(f"Service {_ref(s)}" for s in balancers[:_MAX_LISTED]),
                ("services",),
                unsure_question="All load balancers are internal. Is ingress internal only?",
            )
        )
    return facts


def _entry_point_facts(inputs: Inputs) -> list[Fact]:
    """Facts about ingress classes and Gateway API gateways."""
    facts: list[Fact] = []
    classes = inputs.get("ingressclasses")
    if classes:
        names = [
            c["metadata"]["name"]
            + (
                " (default)"
                if (c["metadata"].get("annotations") or {}).get("ingressclass.kubernetes.io/is-default-class") == "true"
                else ""
            )
            for c in classes
        ]
        facts.append(
            fact(
                "network/ingress-classes",
                "network",
                f"Ingress classes: {', '.join(names)}",
                _evidence("IngressClass", classes),
                ("ingressclasses",),
            )
        )
    gateways = inputs.get("gateways")
    if gateways:
        used = sorted({g.get("spec", {}).get("gatewayClassName", "") for g in gateways} - {""})
        suffix = f" ({', '.join(used)})" if used else ""
        facts.append(
            fact(
                "network/gateways",
                "network",
                f"Gateway API: {_count(len(gateways), 'gateway')}{suffix}",
                _evidence("Gateway", gateways),
                ("gateways", "gatewayclasses"),
            )
        )
    return facts


def network_facts(inputs: Inputs, component_namespaces: set[str] = frozenset(), mesh_seen: bool = False) -> list[Fact]:
    """Facts about network policies, load balancers, ingress classes and gateways.

    Args:
        inputs: What the scan read.
        component_namespaces: Namespaces the catalog's components run in (platform, not applications).
        mesh_seen: Whether a service mesh was seen running.
    """
    return [
        *_policy_facts(inputs, component_namespaces, mesh_seen),
        *_exposure_facts(inputs),
        *_entry_point_facts(inputs),
    ]


# tool -> (label, port, setting, catalog rows whose controller vouches for the namespace)
MONITORING_TOOLS = {
    "prometheus": ("Prometheus", 9090, "PROMETHEUS_URL", ("prometheus", "prometheus-operator")),
    "alertmanager": ("Alertmanager", 9093, "ALERTMANAGER_URL", ("alertmanager", "prometheus-operator")),
}


def monitoring_services(inputs: Inputs, tool: str) -> list[tuple[str, str, int]]:
    """The (namespace, name, port) Services that may lead to a monitoring tool.

    Only namespaces where the catalog saw the tool's controller running count: any
    team can create a Service named `prometheus`, and the scan must not send a query
    (or suggest a URL) to one of theirs. The name must contain the tool's name and
    the Service must expose its port (or forward to it, and then the Service port is
    used); headless and `-operated` Services are skipped,
    since they are not stable entry points.
    """
    _, port, _, keys = MONITORING_TOOLS[tool]
    namespaces = running_namespaces(inputs, *keys)
    found = []
    for svc in inputs.get("services"):
        meta, spec = svc.get("metadata", {}), svc.get("spec", {})
        name, namespace = meta.get("name", ""), meta.get("namespace", "")
        if namespace not in namespaces or tool not in name or name.endswith("-operated"):
            continue
        if spec.get("clusterIP") == "None":
            continue
        # The Service port itself, or one forwarding to the tool's port (prometheus-server: 80 -> 9090).
        for p in spec.get("ports") or []:
            if p.get("port") == port or str(p.get("targetPort")) == str(port):
                found.append((namespace, name, p.get("port")))
                break
    return sorted(found)


def monitoring_facts(inputs: Inputs, prometheus_checked: bool, alertmanager_checked: bool) -> list[Fact]:
    """Where Prometheus and Alertmanager can be reached, as suggested settings.

    With more than one candidate Service the scan does not pick one: it asks.
    """
    if not inputs.ok("services"):
        return []
    facts = []
    for tool, checked in (("prometheus", prometheus_checked), ("alertmanager", alertmanager_checked)):
        label, _, setting, _ = MONITORING_TOOLS[tool]
        found = monitoring_services(inputs, tool)
        sources = ("services", "deployments", "daemonsets", "statefulsets")
        if len(found) > 1:
            names = ", ".join(f"{namespace}/{name}" for namespace, name, _ in found)
            facts.append(
                fact(
                    f"monitoring/{tool}-url",
                    "monitoring",
                    f"Several {label} services in the cluster",
                    tuple(f"Service {namespace}/{name} port {port}" for namespace, name, port in found),
                    sources,
                    unsure_question=f"Several {label} services found: {names}. Which one should the agent use?",
                    scope="cluster",
                )
            )
            continue
        if not found:
            continue
        namespace, name, port = found[0]
        url = f"http://{name}.{namespace}.svc:{port}"
        outcome = "checked: it answered through the API server proxy" if checked else "not checked"
        facts.append(
            fact(
                f"monitoring/{tool}-url",
                "monitoring",
                f"{label} service in the cluster: {url}",
                (f"Service {namespace}/{name} port {port}",),
                sources,
                scope="cluster",
                setting=(setting, url),
                setting_reason=f"{label} service found in namespace {namespace}; {outcome}",
            )
        )
    return facts


NOISY_ALERT_SHARE = 0.5
NOISY_EVENT_MIN = 50
NOISY_EVENT_SHARE = 0.25
NOISY_RESTARTS = 20
STALE_FINDING_DAYS = 3
LONG_FIRING = timedelta(hours=24)

# Only names like these become a suggested setting: the settings are comma-separated
# lists, so a comma or space in a name would smuggle in another entry.
_SETTING_SAFE = {
    "ALERTS_IGNORE_NAMES": re.compile(r"[A-Za-z_:][A-Za-z0-9_:]*"),
    "EVENTS_EXCLUDE_REASONS": re.compile(r"[A-Za-z][A-Za-z0-9]*"),
}


def _noise(key: str, statement: str, question: str, evidence: tuple[str, ...], inputs: tuple[str, ...], setting=None):
    if setting and not _SETTING_SAFE[setting[0]].fullmatch(setting[1]):
        setting = None
    return fact(
        f"noise/{key}",
        "noise",
        statement,
        evidence,
        inputs,
        unsure_question=question,
        scope="cluster",
        setting=setting,
    )


def _utc(when: datetime | None) -> datetime | None:
    """A timestamp without an offset is taken as UTC, so it can be compared with `now`."""
    return when.replace(tzinfo=UTC) if when is not None and when.tzinfo is None else when


def _alert_noise(name: str, statement: str, question: str, evidence: str, source: str) -> Fact:
    return _noise(f"alert/{clean(name)}", statement, question, (evidence,), (source,), ("ALERTS_IGNORE_NAMES", name))


def _alert_facts(
    config: dict,
    shares: dict[str, float] | None,
    firing_now: list[dict] | None,
    now: datetime,
    sources: tuple[str, str],
):
    """Alerts that fire all week (Prometheus), or without history those firing for over a day.

    `sources` names where the Prometheus and the Alertmanager answers came from.
    """
    ignored = set(config["alerts_ignore_names"])
    facts = []
    if shares is not None:
        for name, share in sorted(shares.items()):
            if share < NOISY_ALERT_SHARE or name in ignored:
                continue
            pct = min(100, round(share * 100))
            shown = clean(name)
            facts.append(
                _alert_noise(
                    name,
                    f"`{shown}` fires most of the time ({pct}% of the last 7 days)",
                    f"`{shown}` fired {pct}% of the last 7 days. Is it expected here, so I stop reporting it?",
                    f"{sources[0]}: ALERTS over 7 days",
                    "prometheus",
                )
            )
    elif firing_now is not None:
        seen = set()
        for alert in firing_now:
            name = alert.get("labels", {}).get("alertname", "")
            since = _utc(_parse_time(alert.get("startsAt")))
            if not name or name in ignored or name in seen or since is None or now - since <= LONG_FIRING:
                continue
            seen.add(name)
            date, shown = since.strftime("%Y-%m-%d"), clean(name)
            facts.append(
                _alert_noise(
                    name,
                    f"`{shown}` has been firing for more than a day",
                    f"`{shown}` has been firing since {date}. Is it expected here, so I stop reporting it?",
                    f"{sources[1]}: firing since {since.strftime('%Y-%m-%d %H:%M UTC')}",
                    "alertmanager",
                )
            )
    return facts


def _event_facts(inputs: Inputs, config: dict) -> list[Fact]:
    """Warning event reasons that dominate, unless already hidden by default."""
    counts: Counter = Counter()
    for event in inputs.get("events"):
        counts[event.get("reason", "")] += event.get("count") or 1
    total = sum(counts.values())
    facts = []
    for reason, count in counts.most_common():
        if not reason or reason in config["events_exclude_reasons"]:
            continue
        if count < NOISY_EVENT_MIN or count / total < NOISY_EVENT_SHARE:
            continue
        pct, shown = min(100, round(100 * count / total)), clean(reason)
        facts.append(
            _noise(
                f"event/{shown}",
                f"`{shown}` makes up {pct}% of all warning events",
                f"`{shown}` makes up {pct}% of all warning events. Hide it by default?",
                (f"{count} of {total} warning events with reason {shown}",),
                ("events",),
                ("EVENTS_EXCLUDE_REASONS", reason),
            )
        )
    return facts


def _restart_facts(inputs: Inputs) -> list[Fact]:
    """Running containers with many restarts, one fact per owning workload."""
    worst: dict[tuple[str, str, str], tuple[int, str]] = {}
    for pod in inputs.get("pods"):
        meta = pod.get("metadata", {})
        for status in pod.get("status", {}).get("containerStatuses") or []:
            restarts = status.get("restartCount", 0)
            if restarts < NOISY_RESTARTS or not status.get("ready"):
                continue
            kind, name = _owner(pod)
            key = (meta.get("namespace", ""), kind, name)
            if restarts > worst.get(key, (0, ""))[0]:
                worst[key] = (restarts, meta.get("name", ""))
    facts = []
    for (namespace, kind, name), (restarts, pod_name) in sorted(worst.items()):
        facts.append(
            _noise(
                f"restarts/{namespace}/{kind}/{name}",
                f"{kind} `{namespace}/{name}` restarted {restarts} times but is running",
                f"{kind} `{namespace}/{name}` restarted {restarts} times but is running. Is that by design?",
                (f"Pod {namespace}/{pod_name}: {restarts} restarts",),
                ("pods",),
            )
        )
    return facts


def _stale_finding_facts(findings: list[StoredFinding], now: datetime) -> list[Fact]:
    """Findings that stayed open for days, which may be accepted state."""
    facts = []
    for finding in findings:
        first_seen = _utc(finding.first_seen)
        age = now - first_seen
        if finding.status != "open" or age <= timedelta(days=STALE_FINDING_DAYS):
            continue
        title, fingerprint = clean(finding.title), clean(finding.fingerprint)
        facts.append(
            _noise(
                f"finding/{fingerprint}",
                f'"{title}" has been open for {age.days} days',
                f'"{title}" has been open for {age.days} days. Is it expected here?',
                (f"Finding {fingerprint} open since {first_seen.strftime('%Y-%m-%d')}",),
                ("memory",),
            )
        )
    return facts


def noise_facts(
    inputs: Inputs,
    config: dict,
    shares: dict[str, float] | None,
    firing_now: list[dict] | None,
    findings: list[StoredFinding],
    now: datetime,
    *,
    prometheus_source: str = "Prometheus",
    alertmanager_source: str = "Alertmanager",
) -> list[Fact]:
    """Questions about what looks like background noise: recurring alerts, events, restarts.

    Every fact is `unsure`: the scan can see that something repeats, not whether it is
    meant to. A failed input contributes nothing. The sources name where `shares` and
    `firing_now` came from, for the evidence.
    """
    now = _utc(now)
    facts = _alert_facts(config, shares, firing_now, now, (prometheus_source, alertmanager_source))
    if inputs.ok("events"):
        facts += _event_facts(inputs, config)
    if inputs.ok("pods"):
        facts += _restart_facts(inputs)
    return facts + _stale_finding_facts(findings, now)
