"""Tests for the platform and organisation facts of the scan."""

from fakes import by_key, make_inputs, node, ns

from k8s_servant.scan.discover import organisation_facts, platform_facts


def test_aks_node_pools_and_cloud():
    nodes = [
        node(
            "aks-system-1",
            {
                "kubernetes.azure.com/cluster": "x",
                "kubernetes.azure.com/agentpool": "system",
                "kubernetes.azure.com/mode": "system",
                "node.kubernetes.io/instance-type": "Standard_D4s_v5",
            },
        ),
        *[
            node(
                f"aks-user-{i}",
                {
                    "kubernetes.azure.com/cluster": "x",
                    "kubernetes.azure.com/agentpool": "user",
                    "node.kubernetes.io/instance-type": "Standard_D8s_v5",
                },
            )
            for i in range(3)
        ],
    ]
    version = [{"serverVersion": {"gitVersion": "v1.33.2"}}]
    facts = platform_facts(make_inputs(nodes=nodes, version=version))
    assert by_key(facts, "platform/kubernetes-version").statement == "Kubernetes v1.33.2"
    assert by_key(facts, "platform/cloud").statement == "Managed service: AKS (Azure)"
    assert by_key(facts, "nodepool/user").statement == "Node pool `user`: 3 nodes, 3\u00d7 Standard_D8s_v5"
    assert by_key(facts, "nodepool/system").statement.endswith("(system pool)")
    assert all(f.evidence for f in facts)


def test_instance_types_are_listed_most_first():
    label = "node.kubernetes.io/instance-type"
    pool = {"agentpool": "p"}
    nodes = [
        node("a", {**pool, label: "big"}),
        node("b", {**pool, label: "small"}),
        node("c", {**pool, label: "small"}),
    ]
    facts = platform_facts(make_inputs(nodes=nodes))
    assert by_key(facts, "nodepool/p").statement == "Node pool `p`: 3 nodes, 2\u00d7 small, 1\u00d7 big"


def test_a_bare_provider_id_is_asked_about():
    facts = platform_facts(make_inputs(nodes=[node("n1", {}, provider_id="aws:///eu-west-1a/i-0")]))
    cloud = by_key(facts, "platform/cloud")
    assert cloud.certainty == "unsure"
    assert cloud.question == (
        "Nodes run on AWS, but not with the managed service's labels. Is this a self-managed cluster?"
    )
    assert cloud.evidence


def test_kind_nodes_fall_back_to_roles():
    nodes = [
        node(
            "lab-control-plane",
            {"node-role.kubernetes.io/control-plane": ""},
            provider_id="kind://docker/lab/lab-control-plane",
        )
    ]
    facts = platform_facts(make_inputs(nodes=nodes))
    assert by_key(facts, "nodepool/control-plane")
    cloud = by_key(facts, "platform/cloud")
    assert cloud.statement == "Local kind cluster"
    assert cloud.scope == "platform"


def test_other_nodes_are_workers_and_no_cloud_means_no_fact():
    facts = platform_facts(make_inputs(nodes=[node("n1")]))
    assert by_key(facts, "nodepool/workers").statement == "Node pool `workers`: 1 nodes, "
    assert not [f for f in facts if f.key == "platform/cloud"]


def test_failed_inputs_make_no_facts():
    facts = platform_facts(make_inputs(failed={"nodes": "forbidden", "version": "boom"}))
    assert facts == []
    assert organisation_facts(make_inputs(failed={"namespaces": "forbidden"}), set()) == []


def test_namespace_labels_need_three_application_namespaces():
    namespaces = [ns("kube-system"), ns("shop", {"team": "a"}), ns("pay", {"team": "b"})]
    facts = organisation_facts(make_inputs(namespaces=namespaces), set())
    assert not [f for f in facts if f.key == "organisation/namespace-labels"]


def test_common_namespace_labels_are_reported():
    namespaces = [
        ns("kube-system"),
        ns("a", {"team": "x", "cost-center": "1", "kubernetes.io/metadata.name": "a"}),
        ns("b", {"team": "y", "cost-center": "2"}),
        ns("c", {"team": "z"}),
        ns("d", {"odd": "1"}),
    ]
    facts = organisation_facts(make_inputs(namespaces=namespaces), set())
    labels = by_key(facts, "organisation/namespace-labels")
    assert labels.statement == "Application namespaces carry the labels `team`, `cost-center`"
    assert labels.scope == "platform"
    assert labels.evidence


def test_component_namespaces_count_as_platform():
    namespaces = [ns("kube-system"), ns("kyverno"), ns("shop")]
    f = organisation_facts(make_inputs(namespaces=namespaces), {"kyverno"})[0]
    assert f.key == "organisation/namespaces"
    assert f.statement == "3 namespaces: 2 for the platform, 1 for applications"
    assert f.scope == "cluster"
