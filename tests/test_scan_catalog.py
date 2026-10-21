import re

from fakes import by_key, crd, daemonset, deploy, make_inputs

from k8s_servant.scan.catalog import CATALOG, component_facts


def test_every_catalog_row_is_valid():
    keys = [c.key for c in CATALOG]
    assert len(keys) == len(set(keys))
    for c in CATALOG:
        assert c.topic in {"components", "mesh", "secrets", "monitoring", "network"}
        assert c.crd_groups or c.workloads or c.webhooks
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
    assert len(f.evidence) <= 3 and "`a, b, c`" in f.statement and namespaces == {"a", "b", "c"}
