"""Platform, organisation and network facts: what the cluster runs on, how it is split
up and how traffic is kept in check.

Why: the Kubernetes version, the managed service, the node pools and the namespace
layout are the first things an environment description states, and all of them can
be read off the nodes and namespaces without asking anyone. The same holds for
network policies, load balancers and ingress classes. A fact is only made
from an input that was actually read; a failed read never becomes a statement.
"""

from collections import Counter

from k8s_servant.scan.facts import Fact, fact
from k8s_servant.scan.inputs import Inputs

_INSTANCE_TYPE = "node.kubernetes.io/instance-type"

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


def _labels(item: dict) -> dict:
    return item.get("metadata", {}).get("labels") or {}


def _pool_of(node: dict) -> tuple[str, str]:
    """The pool a node belongs to, and the evidence label for it."""
    labels = _labels(node)
    for label in _POOL_LABELS:
        if label in labels:
            return labels[label], label
    if "node-role.kubernetes.io/control-plane" in labels:
        return "control-plane", "node-role.kubernetes.io/control-plane"
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
                (f"{count} nodes with label {label}",),
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
                (f"{count} nodes with providerID {prefix}",),
                ("nodes",),
                unsure_question=(
                    f"Nodes run on {cloud}, but not with the managed service's labels. Is this a self-managed cluster?"
                ),
                scope="platform",
            )
    return None


def _pool_fact(pool: str, members: list[tuple[dict, str]]) -> Fact:
    """One fact per pool name; the evidence lists each label that named the pool."""
    nodes = [n for n, _ in members]
    types = Counter(_labels(n).get(_INSTANCE_TYPE) for n in nodes)
    types.pop(None, None)
    listed = ", ".join(f"{count}\u00d7 {name}" for name, count in types.most_common())
    statement = f"Node pool `{pool}`: {len(nodes)} node{'s' if len(nodes) != 1 else ''}"
    if listed:
        statement += f", {listed}"
    if any(_labels(n).get("kubernetes.azure.com/mode") == "system" for n in nodes):
        statement += " (system pool)"
    by_label = Counter(label for _, label in members)
    evidence = tuple(
        f"{count} nodes with label {label}={pool}" if label else "kubectl get nodes"
        for label, count in by_label.items()
    )
    return fact(f"nodepool/{pool}", "platform", statement, evidence, ("nodes",))


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
                    f"Kubernetes {version}",
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


def organisation_facts(inputs: Inputs, component_namespaces: set[str]) -> list[Fact]:
    """Facts about how the namespaces are split between platform and applications."""
    if not inputs.ok("namespaces"):
        return []
    names = {n.get("metadata", {}).get("name", ""): n for n in inputs.get("namespaces")}
    names.pop("", None)
    platform = {name for name in names if name.startswith("kube-") or name == "default" or name in component_namespaces}
    apps = [n for name, n in names.items() if name not in platform]
    facts = [
        fact(
            "organisation/namespaces",
            "organisation",
            f"{len(names)} namespaces: {len(platform)} for the platform, {len(apps)} for applications",
            ("kubectl get namespaces",),
            ("namespaces",),
            scope="cluster",
        )
    ]
    if len(apps) >= _MIN_LABELLED_NAMESPACES:
        counts = Counter(key for n in apps for key in _labels(n) if "kubernetes.io" not in key and "k8s.io" not in key)
        common = [
            key for key, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if count * 2 >= len(apps)
        ]
        if common:
            listed = ", ".join(f"`{key}`" for key in common)
            facts.append(
                fact(
                    "organisation/namespace-labels",
                    "organisation",
                    f"Application namespaces carry the labels {listed}",
                    ("kubectl get namespaces",),
                    ("namespaces",),
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


def _policy_facts(inputs: Inputs) -> list[Fact]:
    """Default-deny and missing-policy facts from NetworkPolicies, Calico and Cilium."""
    if not inputs.ok("networkpolicies"):
        return []
    policies = inputs.get("networkpolicies")
    facts: list[Fact] = []
    if not policies:
        # Calico or Cilium may restrict traffic where no NetworkPolicy exists.
        others = (
            bool(inputs.crd_names() & _POLICY_CRDS)
            or any(inputs.get(name) for name in ("calico-policies", "cilium-policies"))
            or any(name in inputs.failed for name in ("calico-policies", "cilium-policies", "crds"))
        )
        facts.append(
            fact(
                "network/no-network-policies",
                "network",
                "No NetworkPolicies: pod traffic inside the cluster is not restricted",
                ("kubectl get networkpolicies -A",),
                ("networkpolicies", "crds", "calico-policies", "cilium-policies"),
                unsure_question=(
                    "No NetworkPolicies, but Calico or Cilium policies may restrict traffic. Which rules apply?"
                    if others
                    else ""
                ),
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
        apps = {
            _ref(n)
            for n in inputs.get("namespaces")
            if not _ref(n).startswith("kube-") and _ref(n) not in ("default", "")
        }
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
                    f"Inbound pod traffic is denied by default in {len(covered)} of "
                    f"{_count(len(apps), 'application namespace')}",
                    _evidence("NetworkPolicy", [p for group in covered.values() for p in group]),
                    ("networkpolicies", "namespaces"),
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


def network_facts(inputs: Inputs) -> list[Fact]:
    """Facts about network policies, load balancers, ingress classes and gateways."""
    return [*_policy_facts(inputs), *_exposure_facts(inputs), *_entry_point_facts(inputs)]
