"""Platform and organisation facts: what the cluster runs on and how it is split up.

Why: the Kubernetes version, the managed service, the node pools and the namespace
layout are the first things an environment description states, and all of them can
be read off the nodes and namespaces without asking anyone. A fact is only made
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
