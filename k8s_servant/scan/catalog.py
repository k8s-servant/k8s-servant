"""A catalog of well-known cluster components, and how to recognise them.

Why: most of what an environment description says ("Kyverno enforces policy",
"traffic goes through Istio") can be read off the cluster by matching CRD API
groups, workload names and webhook configurations against the upstream charts'
default names. Keeping that knowledge as data (one row per component) makes a new
component a one-line change, and keeps the matching rules in one place.

A row is *proven* only when its controller runs and, where the row declares an
API (CRD groups or webhooks), that API is installed too. Half-installed or
left-over components become questions for a human, never plain statements.
"""

import re
from dataclasses import dataclass

from k8s_servant.scan.facts import Fact, fact
from k8s_servant.scan.inputs import Inputs


@dataclass(frozen=True)
class Component:
    """One well-known component and the traces it leaves in a cluster."""

    key: str
    name: str
    topic: str  # components | mesh | secrets | monitoring | network
    role: str
    crd_groups: tuple[str, ...] = ()
    workloads: tuple[str, ...] = ()  # regexes, full match on Deployment/DaemonSet/StatefulSet names
    webhooks: tuple[str, ...] = ()  # regexes, full match on webhook configuration names
    namespaces: tuple[str, ...] = ()  # exact names its charts install into by default
    # Set when the workloads alone do not prove the component is used: running then
    # only raises this question, never a plain statement.
    only_ask: str = ""


def _c(key, name, topic, role, *, crds=(), workloads=(), webhooks=(), namespaces=(), only_ask=""):
    return Component(
        key, name, topic, role, tuple(crds), tuple(workloads), tuple(webhooks), tuple(namespaces), only_ask
    )


# A row declares crds/webhooks only when its install always has them: a running
# controller without a declared API is then reported as a half-installed component.
CATALOG: tuple[Component, ...] = (
    _c("flux", "Flux", "components", "GitOps controller", crds=("source.toolkit.fluxcd.io", "kustomize.toolkit.fluxcd.io", "helm.toolkit.fluxcd.io", "notification.toolkit.fluxcd.io", "image.toolkit.fluxcd.io"), workloads=("source-controller", "kustomize-controller", "helm-controller", "notification-controller"), namespaces=("flux-system",)),
    _c("argo-cd", "Argo CD", "components", "GitOps controller", crds=("argoproj.io",), workloads=(r".*argocd-(server|repo-server|application-controller|applicationset-controller)",), namespaces=("argocd",)),
    _c("kyverno", "Kyverno", "components", "policy engine", crds=("kyverno.io",), workloads=("kyverno-admission-controller", "kyverno"), namespaces=("kyverno",)),
    _c("gatekeeper", "OPA Gatekeeper", "components", "policy engine", crds=("constraints.gatekeeper.sh", "templates.gatekeeper.sh", "config.gatekeeper.sh"), workloads=("gatekeeper-controller-manager", "gatekeeper-audit"), namespaces=("gatekeeper-system",)),
    _c("cert-manager", "cert-manager", "components", "certificate manager", crds=("cert-manager.io",), workloads=("cert-manager", "cert-manager-webhook", "cert-manager-cainjector"), namespaces=("cert-manager",)),
    _c("ingress-nginx", "ingress-nginx", "components", "ingress controller", workloads=(r"(.+-)?ingress-nginx-controller", "nginx-ingress-controller"), namespaces=("ingress-nginx",)),
    _c("traefik", "Traefik", "components", "ingress controller", workloads=("traefik", ".+-traefik"), namespaces=("traefik",)),
    _c("envoy-gateway", "Envoy Gateway", "components", "gateway controller", crds=("gateway.envoyproxy.io",), workloads=("envoy-gateway",), namespaces=("envoy-gateway-system",)),
    _c("keda", "KEDA", "components", "event-driven autoscaler", crds=("keda.sh",), workloads=("keda-operator", "keda-operator-metrics-apiserver"), namespaces=("keda",)),
    _c("cluster-autoscaler", "Cluster Autoscaler", "components", "node autoscaler", workloads=(r"(.+-)?cluster-autoscaler(-.+)?",)),
    _c("karpenter", "Karpenter", "components", "node autoscaler", crds=("karpenter.sh",), workloads=("karpenter",), namespaces=("karpenter",)),
    _c("velero", "Velero", "components", "backup", crds=("velero.io",), workloads=("velero",), namespaces=("velero",)),
    _c("reloader", "Reloader", "components", "rolling restart on config change", workloads=(r"(.+-)?reloader(-reloader)?",), namespaces=("reloader",)),
    _c("descheduler", "Descheduler", "components", "pod rebalancer", workloads=("descheduler", ".+-descheduler")),
    _c("metrics-server", "metrics-server", "components", "resource metrics", workloads=(r"(.+-)?metrics-server(-.+)?",)),
    _c("istio", "Istio", "mesh", "service mesh", crds=("networking.istio.io", "security.istio.io", "telemetry.istio.io"), workloads=(r"istiod(-.+)?",), webhooks=("istio-sidecar-injector.*", "istio-revision-tag-.+", "istiod-.+"), namespaces=("istio-system",)),
    _c("linkerd", "Linkerd", "mesh", "service mesh", crds=("linkerd.io", "policy.linkerd.io"), workloads=("linkerd-destination", "linkerd-identity", "linkerd-proxy-injector"), webhooks=(r"linkerd-.+-webhook-config",), namespaces=("linkerd",)),
    _c("consul", "Consul", "mesh", "service mesh", crds=("consul.hashicorp.com",), workloads=(r"(.+-)?consul-(server|client|connect-injector(-webhook-deployment)?)", r"(.+-)?consul-webhook-cert-manager"), webhooks=(r".*consul-connect-injector.*",), namespaces=("consul",)),
    _c("cilium-mesh", "Cilium service mesh", "mesh", "service mesh", workloads=("cilium-envoy",), only_ask="Cilium Envoy runs. Do you use Cilium's service mesh features?"),
    _c("istio-egress-gateway", "Istio egress gateway", "mesh", "controlled outbound traffic", workloads=("istio-egress.*",)),
    _c("external-secrets", "External Secrets Operator", "secrets", "secret sync from an external store", crds=("external-secrets.io",), workloads=(r"(.+-)?external-secrets(-webhook|-cert-controller)?",), namespaces=("external-secrets",)),
    _c("secrets-store-csi", "Secrets Store CSI Driver", "secrets", "mounts external secrets as volumes", crds=("secrets-store.csi.x-k8s.io",), workloads=(r".*secrets-store-csi-driver", "csi-secrets-store")),
    _c("csi-provider-azure", "Azure Key Vault CSI provider", "secrets", "secret provider", workloads=(r".*secrets-store-provider-azure",)),
    _c("csi-provider-aws", "AWS Secrets Manager CSI provider", "secrets", "secret provider", workloads=(r".*(secrets-store-provider-aws|csi-driver-provider-aws)",)),
    _c("csi-provider-gcp", "Google Secret Manager CSI provider", "secrets", "secret provider", workloads=(r".*(secrets-store-provider-gcp|csi-driver-provider-gcp)",)),
    _c("vault-csi-provider", "Vault CSI provider", "secrets", "secret provider", workloads=(r".*vault-csi-provider",)),
    _c("sealed-secrets", "Sealed Secrets", "secrets", "encrypted secrets in Git", crds=("bitnami.com",), workloads=(r"(.+-)?sealed-secrets(-controller)?",)),
    _c("vault-injector", "Vault Agent Injector", "secrets", "secret injection by sidecar", workloads=(r"(.+-)?(vault-)?agent-injector",)),
    _c("prometheus-operator", "Prometheus Operator", "monitoring", "runs Prometheus and Alertmanager", crds=("monitoring.coreos.com",), workloads=(r".*prometheus(-stack)?-operator", "prometheus-operator.*", r".*kube-prom-operator")),
    _c("prometheus", "Prometheus", "monitoring", "metrics store", workloads=(r"(.+-)?prometheus-server", "prometheus-.+-prometheus", "prometheus(-k8s)?")),
    _c("alertmanager", "Alertmanager", "monitoring", "alert routing", workloads=(r"alertmanager|alertmanager-main|.+-alertmanager",)),
    _c("grafana", "Grafana", "monitoring", "dashboards", workloads=(r"(.+-)?grafana",)),
    _c("loki", "Loki", "monitoring", "log store", workloads=(r"loki|loki-(read|write|backend|gateway|distributor|ingester|querier|query-frontend|compactor|index-gateway|canary)|.+-loki",)),
    _c("alloy", "Grafana Alloy", "monitoring", "telemetry collector", workloads=(r"(.+-)?alloy(-.+)?",)),
    _c("log-shipper", "Log shipper", "monitoring", "ships logs to a store", workloads=(r"(.+-)?(promtail|fluent-?bit|fluentd|vector)(-.+)?",)),
    _c("opentelemetry-collector", "OpenTelemetry Collector", "monitoring", "telemetry pipeline", workloads=(r"(.+-)?(opentelemetry-collector|otel-collector)(-.+)?",)),
    _c("opentelemetry-operator", "OpenTelemetry Operator", "monitoring", "manages collectors", crds=("opentelemetry.io",), workloads=(r"(.+-)?opentelemetry-operator(-controller-manager)?",)),
    _c("calico", "Calico", "network", "CNI and network policy", crds=("crd.projectcalico.org", "projectcalico.org"), workloads=("calico-node", "calico-kube-controllers", "calico-typha", "tigera-operator"), namespaces=("calico-system", "tigera-operator")),
    _c("cilium", "Cilium", "network", "CNI and network policy", workloads=("cilium", r"cilium-operator(-.+)?", "anetd")),
    _c("azure-cni", "Azure CNI", "network", "CNI", workloads=(r"azure-cns(-.+)?", r"azure-cni-.+")),
    _c("aws-vpc-cni", "AWS VPC CNI", "network", "CNI", workloads=("aws-node",)),
    _c("kindnet", "kindnet", "network", "CNI", workloads=("kindnet",)),
    _c("flannel", "Flannel", "network", "CNI", workloads=(r"(kube-)?flannel(-ds.*)?",), namespaces=("kube-flannel",)),
)  # fmt: skip

CATALOG_INPUTS = (
    "crds",
    "deployments",
    "daemonsets",
    "statefulsets",
    "mutatingwebhooks",
    "validatingwebhooks",
    "namespaces",
)

_WORKLOAD_INPUTS = (
    ("deployments", "Deployment", "readyReplicas"),
    ("daemonsets", "DaemonSet", "numberReady"),
    ("statefulsets", "StatefulSet", "readyReplicas"),
)
_WEBHOOK_INPUTS = (
    ("mutatingwebhooks", "MutatingWebhookConfiguration"),
    ("validatingwebhooks", "ValidatingWebhookConfiguration"),
)
_MAX_EVIDENCE = 3


def _matches(patterns: tuple[str, ...], name: str) -> bool:
    return any(re.fullmatch(p, name) for p in patterns)


def _running(inputs: Inputs, component: Component) -> list[tuple[str, str]]:
    """Ready workloads of the component as (namespace, evidence) pairs."""
    found = []
    for input_name, kind, ready_field in _WORKLOAD_INPUTS:
        for item in inputs.get(input_name):
            meta = item.get("metadata", {})
            ready = (item.get("status") or {}).get(ready_field) or 0
            if ready > 0 and _matches(component.workloads, meta.get("name", "")):
                namespace = meta.get("namespace", "")
                found.append((namespace, f"{kind} {namespace}/{meta['name']} ({ready} ready)"))
    return found


def _api_evidence(inputs: Inputs, component: Component) -> list[str]:
    """Evidence that the component's CRDs or webhooks are installed."""
    found = []
    groups = sorted(inputs.crd_groups() & set(component.crd_groups))
    if groups:
        found.append(f"CRD group {groups[0]}")
    for input_name, kind in _WEBHOOK_INPUTS:
        for item in inputs.get(input_name):
            name = item.get("metadata", {}).get("name", "")
            if _matches(component.webhooks, name):
                found.append(f"{kind} {name}")
    return found


def _judge(inputs: Inputs, component: Component) -> tuple[Fact | None, set[str]]:
    """One component's fact (None when it left no trace) and the namespaces it runs in."""
    running = _running(inputs, component)
    api = _api_evidence(inputs, component)
    running_namespaces = {ns for ns, _ in running}
    evidence = tuple((api[:1] + [e for _, e in running] + api[1:])[:_MAX_EVIDENCE])
    key, name = f"component/{component.key}", component.name
    question = ""
    # An unreadable input cannot show absence: skip the questions that rest on it.
    crds_unread = "crds" in inputs.failed
    workloads_unread = any(i in inputs.failed for i, _, _ in _WORKLOAD_INPUTS)
    if running and component.only_ask:
        statement = f"{name} workloads run in namespace `{', '.join(sorted(running_namespaces))}`"
        question = component.only_ask
    elif running and (api or crds_unread or not (component.crd_groups or component.webhooks)):
        statement = f"{name} ({component.role}) runs in namespace `{', '.join(sorted(running_namespaces))}`"
    elif api and not workloads_unread:
        statement = f"{name} CRDs or webhooks are installed"
        question = (
            f"{name} CRDs or webhooks are installed but no {name} controller is running. Left over from an uninstall?"
        )
    elif running:
        statement = f"{name} runs without its CRDs"
        question = f"{name} runs but its CRDs are missing. Is it installed fully?"
    else:
        existing = sorted(
            {n.get("metadata", {}).get("name", "") for n in inputs.get("namespaces")} & set(component.namespaces)
        )
        if not existing or workloads_unread:
            return None, running_namespaces
        statement = f"Namespace `{existing[0]}` exists"
        evidence = (f"Namespace {existing[0]}",)
        question = f"Namespace `{existing[0]}` exists but no {name} workload runs. Is {name} used here?"
    return fact(key, component.topic, statement, evidence, CATALOG_INPUTS, unsure_question=question), running_namespaces


def component_facts(inputs: Inputs) -> tuple[list[Fact], set[str]]:
    """Facts about the catalog components found, and the namespaces they run in.

    The namespaces let the caller tell system namespaces from business ones.
    """
    facts: list[Fact] = []
    namespaces: set[str] = set()
    for component in CATALOG:
        found, running_namespaces = _judge(inputs, component)
        namespaces |= running_namespaces
        if found:
            facts.append(found)
    return facts, namespaces
