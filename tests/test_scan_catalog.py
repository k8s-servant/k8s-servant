import re

from fakes import by_key, crd, daemonset, deploy, make_inputs, statefulset

from k8s_servant.scan.catalog import CATALOG, component_facts


def test_every_catalog_row_is_valid():
    keys = [c.key for c in CATALOG]
    assert len(keys) == len(set(keys))
    for c in CATALOG:
        assert c.topic in {"components", "mesh", "secrets", "monitoring", "network"}
        assert c.crd_groups or c.crd_names or c.workloads or c.webhooks
        for pattern in c.workloads + c.webhooks:
            re.compile(pattern)


def test_crds_and_a_running_controller_are_seen():
    facts, namespaces = component_facts(
        make_inputs(
            crds=[crd("kyverno.io")],
            deployments=[deploy("kyverno", "kyverno-admission-controller", ready=3)],
        )
    )
    f = by_key(facts, "component/kyverno")
    assert f.certainty == "seen" and "kyverno" in namespaces
    assert "Deployment kyverno/kyverno-admission-controller (3 ready)" in f.evidence
    assert f.statement == "Kyverno (policy engine) runs in namespace `kyverno`"


def test_crds_without_a_controller_are_asked_about():
    facts, _ = component_facts(make_inputs(crds=[crd("kyverno.io")]))
    assert by_key(facts, "component/kyverno").question.endswith("Left over from an uninstall?")


def test_a_controller_with_zero_ready_replicas_is_not_running():
    facts, _ = component_facts(
        make_inputs(
            crds=[crd("kyverno.io")],
            deployments=[deploy("kyverno", "kyverno-admission-controller", ready=0)],
        )
    )
    assert by_key(facts, "component/kyverno").certainty == "unsure"


def test_a_component_without_crds_is_seen_from_its_workload():
    facts, _ = component_facts(make_inputs(daemonsets=[daemonset("kube-system", "kindnet", ready=1)]))
    assert by_key(facts, "component/kindnet").topic == "network"


def test_nothing_matched_gives_no_facts():
    assert component_facts(make_inputs()) == ([], set())


def test_a_running_controller_without_its_crds_is_asked_about():
    facts, _ = component_facts(make_inputs(deployments=[deploy("kyverno", "kyverno-admission-controller")]))
    assert (
        by_key(facts, "component/kyverno").question == "Kyverno runs but its CRDs are missing. Is it installed fully?"
    )


def test_a_webhook_counts_as_the_components_api():
    facts, _ = component_facts(
        make_inputs(
            deployments=[deploy("istio-system", "istiod")],
            mutatingwebhooks=[{"metadata": {"name": "istio-sidecar-injector"}}],
        )
    )
    f = by_key(facts, "component/istio")
    assert f.certainty == "seen"
    assert "MutatingWebhookConfiguration istio-sidecar-injector" in f.evidence


def test_a_lone_catalog_namespace_is_asked_about():
    facts, _ = component_facts(make_inputs(namespaces=[{"metadata": {"name": "velero"}}]))
    assert by_key(facts, "component/velero").question == (
        "Namespace `velero` exists but no Velero workload runs. Is Velero used here?"
    )


def test_evidence_has_at_most_three_items_and_namespaces_are_joined():
    facts, namespaces = component_facts(
        make_inputs(
            crds=[crd("kyverno.io")],
            deployments=[deploy(n, "kyverno-admission-controller") for n in ["c", "a", "b"]] + [deploy("b", "kyverno")],
        )
    )
    f = by_key(facts, "component/kyverno")
    assert len(f.evidence) <= 3 and namespaces == {"a", "b", "c"}
    assert f.statement == "Kyverno (policy engine) runs in namespaces `a`, `b`, `c`"


def _keys(facts):
    return {f.key: f for f in facts}


def test_plain_cilium_with_envoy_never_gives_a_seen_mesh_fact():
    facts, _ = component_facts(
        make_inputs(daemonsets=[daemonset("kube-system", "cilium"), daemonset("kube-system", "cilium-envoy")])
    )
    mesh = _keys(facts)["component/cilium-mesh"]
    assert mesh.certainty == "unsure" and mesh.question.startswith("Cilium Envoy runs.")
    assert _keys(facts)["component/cilium"].certainty == "seen"


def test_an_otel_operator_alone_is_not_a_collector():
    facts, _ = component_facts(
        make_inputs(
            crds=[crd("opentelemetry.io")],
            deployments=[deploy("otel", "opentelemetry-operator-controller-manager")],
        )
    )
    assert "component/opentelemetry-collector" not in _keys(facts)
    assert _keys(facts)["component/opentelemetry-operator"].certainty == "seen"


def test_a_collector_without_crds_is_seen():
    facts, _ = component_facts(make_inputs(deployments=[deploy("obs", "otel-collector")]))
    assert _keys(facts)["component/opentelemetry-collector"].certainty == "seen"


def test_alertmanager_bridges_are_not_alertmanager():
    facts, _ = component_facts(
        make_inputs(deployments=[deploy("obs", "alertmanager-webhook-teams"), deploy("obs", "alertmanager-bot")])
    )
    assert "component/alertmanager" not in _keys(facts)


def test_real_alertmanager_names_match():
    for name in ("alertmanager-main", "alertmanager-kps-alertmanager", "prometheus-alertmanager", "alertmanager"):
        facts, _ = component_facts(make_inputs(statefulsets=[statefulset("obs", name)]))
        assert _keys(facts)["component/alertmanager"].certainty == "seen", name


def test_loki_promtail_is_a_log_shipper_not_loki():
    facts, _ = component_facts(make_inputs(daemonsets=[daemonset("obs", "loki-promtail")]))
    assert "component/loki" not in _keys(facts)
    assert _keys(facts)["component/log-shipper"].certainty == "seen"


def test_alloy_is_not_a_log_shipper():
    facts, _ = component_facts(make_inputs(daemonsets=[daemonset("obs", "alloy")]))
    assert "component/log-shipper" not in _keys(facts)
    assert _keys(facts)["component/alloy"].certainty == "seen"


def test_unreadable_crds_do_not_make_a_running_controller_unsure():
    facts, _ = component_facts(
        make_inputs(
            failed={"crds": "Forbidden"},
            deployments=[deploy("kyverno", "kyverno-admission-controller")],
        )
    )
    assert _keys(facts)["component/kyverno"].certainty == "seen"


def test_unreadable_workloads_do_not_make_crds_a_leftover():
    facts, _ = component_facts(
        make_inputs(
            failed={"deployments": "Forbidden"},
            crds=[crd("kyverno.io")],
            namespaces=[{"metadata": {"name": "velero"}}],
        )
    )
    assert facts == []


def test_argo_rollouts_alone_is_not_argo_cd():
    facts, _ = component_facts(
        make_inputs(
            crds=[crd("argoproj.io", "rollouts.argoproj.io")],
            deployments=[deploy("argo-rollouts", "argo-rollouts")],
        )
    )
    assert "component/argo-cd" not in _keys(facts)


def test_monitoring_crds_alone_are_not_a_leftover_operator():
    crds = [crd("monitoring.coreos.com", "servicemonitors.monitoring.coreos.com")]
    facts, _ = component_facts(make_inputs(crds=crds, deployments=[], daemonsets=[], statefulsets=[]))
    assert "component/prometheus-operator" not in _keys(facts)


def test_argo_cd_is_proven_by_its_application_crd():
    facts, _ = component_facts(
        make_inputs(
            crds=[crd("argoproj.io", "applications.argoproj.io")],
            deployments=[deploy("argocd", "argocd-server")],
        )
    )
    f = by_key(facts, "component/argo-cd")
    assert f.certainty == "seen" and "CRD applications.argoproj.io" in f.evidence
