"""Tests for the platform and organisation facts of the scan."""

from fakes import by_key, crd, gateway, ingressclass, make_inputs, netpol, node, ns, service

from k8s_servant.scan.discover import network_facts, organisation_facts, platform_facts


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
    assert by_key(facts, "nodepool/workers").statement == "Node pool `workers`: 1 node"
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


def test_a_pool_without_instance_types_ends_after_the_count():
    facts = platform_facts(make_inputs(nodes=[node(f"n{i}", {"agentpool": "p"}) for i in range(3)]))
    assert by_key(facts, "nodepool/p").statement == "Node pool `p`: 3 nodes"


def test_one_pool_name_is_one_fact_even_with_different_labels():
    nodes = [node("a", {"agentpool": "x"}), node("b", {"kubernetes.azure.com/agentpool": "x"})]
    pools = [f for f in platform_facts(make_inputs(nodes=nodes)) if f.key == "nodepool/x"]
    assert len(pools) == 1
    assert pools[0].statement == "Node pool `x`: 2 nodes"
    assert len(pools[0].evidence) == 2


def test_default_deny_ingress_counts_application_namespaces():
    pols = [netpol("shop", {}, ["Ingress"]), netpol("kube-system", {}, ["Ingress"], name="x")]
    namespaces = [ns("shop"), ns("blog"), ns("kube-system")]
    f = by_key(network_facts(make_inputs(networkpolicies=pols, namespaces=namespaces)), "network/default-deny-ingress")
    assert f.statement == "Inbound pod traffic is denied by default in 1 of 2 application namespaces"
    assert f.certainty == "seen"
    assert f.evidence == ("NetworkPolicy shop/default-deny",)


def test_policy_types_missing_means_ingress_and_rules_disqualify():
    deny = netpol("shop", {}, [])
    open_ = netpol("blog", {}, ["Ingress"], ingress=[{"from": [{"podSelector": {}}]}])
    facts = network_facts(make_inputs(networkpolicies=[deny, open_], namespaces=[ns("shop"), ns("blog")]))
    assert by_key(facts, "network/default-deny-ingress").statement.endswith("in 1 of 2 application namespaces")


def test_default_deny_egress_with_exceptions_is_asked():
    pol = netpol(
        "shop",
        pod_selector={},
        types=["Egress"],
        egress=[{"ports": [{"port": 53, "protocol": "UDP"}]}, {"to": [{"ipBlock": {"cidr": "10.0.0.0/8"}}]}],
    )
    f = by_key(
        network_facts(make_inputs(networkpolicies=[pol], namespaces=[ns("shop")])), "network/default-deny-egress"
    )
    assert f.certainty == "unsure"
    assert f.statement == "Outbound pod traffic is denied by default in 1 namespace, except DNS, 10.0.0.0/8"
    assert f.question == "I read this as: outbound traffic is denied by default except DNS, 10.0.0.0/8. Correct?"


def test_egress_exceptions_include_namespace_labels_and_are_capped():
    to = [{"ipBlock": {"cidr": f"10.0.{i}.0/24"}} for i in range(6)]
    to.append({"namespaceSelector": {"matchLabels": {"team": "a"}}})
    pol = netpol("shop", {}, ["Egress"], egress=[{"to": to}])
    f = by_key(network_facts(make_inputs(networkpolicies=[pol])), "network/default-deny-egress")
    assert f.statement.endswith("except 10.0.0.0/24, 10.0.1.0/24, 10.0.2.0/24, 10.0.3.0/24, 10.0.4.0/24, and more")
    ns_rule = netpol("a", {}, ["Egress"], egress=[{"to": [{"namespaceSelector": {"matchLabels": {"team": "a"}}}]}])
    g = by_key(network_facts(make_inputs(networkpolicies=[ns_rule])), "network/default-deny-egress")
    assert g.statement.endswith("except namespaces labelled team=a")


def test_deny_all_egress_is_seen():
    pol = netpol("shop", pod_selector={}, types=["Egress"], egress=[])
    f = by_key(
        network_facts(make_inputs(networkpolicies=[pol], namespaces=[ns("shop")])), "network/default-deny-egress"
    )
    assert f.certainty == "seen"
    assert f.statement == "Outbound pod traffic is denied by default in 1 namespace"


def test_a_failed_policy_read_makes_no_policy_fact():
    inputs = make_inputs(failed={"networkpolicies": "Forbidden"}, namespaces=[ns("shop")])
    assert not [f for f in network_facts(inputs) if f.key.startswith("network/default-deny")]
    assert "network/no-network-policies" not in {f.key for f in network_facts(inputs)}


def test_no_policies_is_seen_when_nothing_else_could_restrict():
    f = by_key(network_facts(make_inputs(networkpolicies=[])), "network/no-network-policies")
    assert f.certainty == "seen"
    assert f.statement == "No NetworkPolicies: pod traffic inside the cluster is not restricted"
    assert f.evidence


def test_no_policies_is_unsure_when_calico_could_not_be_read():
    inputs = make_inputs(networkpolicies=[], failed={"calico-policies": "RuntimeError: Forbidden"})
    f = by_key(network_facts(inputs), "network/no-network-policies")
    assert f.certainty == "unsure"
    assert f.question == "No NetworkPolicies, but Calico or Cilium policies may restrict traffic. Which rules apply?"


def test_calico_and_cilium_policies_are_counted():
    inputs = make_inputs(
        networkpolicies=[],
        **{"calico-policies": [{"metadata": {"name": "a"}}], "cilium-policies": [{"metadata": {"name": "b"}}] * 2},
    )
    facts = network_facts(inputs)
    assert by_key(facts, "network/calico-policies").statement == "1 Calico global network policy"
    assert by_key(facts, "network/cilium-policies").statement == "2 Cilium cluster-wide network policies"
    assert by_key(facts, "network/no-network-policies").certainty == "unsure"


def test_internal_load_balancers_ask_about_ingress():
    svc = service(
        "ingress-nginx",
        "ingress-nginx-controller",
        "LoadBalancer",
        {"service.beta.kubernetes.io/azure-load-balancer-internal": "true"},
    )
    facts = {f.key: f for f in network_facts(make_inputs(services=[svc]))}
    lb = facts["network/lb/ingress-nginx/ingress-nginx-controller"]
    assert lb.statement == "LoadBalancer service `ingress-nginx/ingress-nginx-controller` is internal-only"
    assert lb.scope == "cluster"
    assert lb.evidence == ("service.beta.kubernetes.io/azure-load-balancer-internal: true",)
    assert facts["network/ingress-exposure"].certainty == "unsure"
    assert facts["network/ingress-exposure"].question == "All load balancers are internal. Is ingress internal only?"


def test_other_clouds_internal_annotations():
    for key, value in (
        ("service.beta.kubernetes.io/aws-load-balancer-internal", "true"),
        ("service.beta.kubernetes.io/aws-load-balancer-scheme", "internal"),
        ("networking.gke.io/load-balancer-type", "Internal"),
        ("cloud.google.com/load-balancer-type", "Internal"),
    ):
        facts = network_facts(make_inputs(services=[service("a", "b", "LoadBalancer", {key: value})]))
        assert by_key(facts, "network/lb/a/b").statement.endswith("is internal-only"), key


def test_a_public_load_balancer_needs_no_question():
    facts = network_facts(make_inputs(services=[service("a", "b", "LoadBalancer", {}), service("a", "c")]))
    keys = {f.key for f in facts}
    assert "network/ingress-exposure" not in keys
    assert "network/lb/a/c" not in keys
    lb = by_key(facts, "network/lb/a/b")
    assert lb.statement == "LoadBalancer service `a/b` has no internal-only annotation"
    assert lb.evidence


def test_a_mix_of_load_balancers_needs_no_question():
    internal = {"service.beta.kubernetes.io/azure-load-balancer-internal": "true"}
    svcs = [service("a", "x", "LoadBalancer", internal), service("a", "y", "LoadBalancer", {})]
    assert "network/ingress-exposure" not in {f.key for f in network_facts(make_inputs(services=svcs))}


def test_failed_services_make_no_load_balancer_facts():
    assert network_facts(make_inputs(failed={"services": "Forbidden"})) == []


def test_ingress_classes_name_the_default():
    classes = [ingressclass("nginx", default=True), ingressclass("internal")]
    f = by_key(network_facts(make_inputs(ingressclasses=classes)), "network/ingress-classes")
    assert f.statement == "Ingress classes: nginx (default), internal"
    assert f.evidence == ("IngressClass nginx", "IngressClass internal")


def test_gateways_list_their_classes():
    gws = [gateway("a", "g1", "istio"), gateway("a", "g2", "istio"), gateway("b", "g3", "nginx")]
    f = by_key(network_facts(make_inputs(gateways=gws, gatewayclasses=[])), "network/gateways")
    assert f.statement == "Gateway API: 3 gateways (istio, nginx)"
    assert f.evidence == ("Gateway a/g1", "Gateway a/g2", "Gateway b/g3")
    one = by_key(network_facts(make_inputs(gateways=gws[:1])), "network/gateways")
    assert one.statement == "Gateway API: 1 gateway (istio)"


def test_an_allow_all_policy_in_the_same_namespace_cancels_the_deny():
    deny = netpol("shop", {}, ["Ingress", "Egress"])
    allow = netpol("shop", {}, ["Ingress", "Egress"], ingress=[{}], egress=[{}], name="allow-all")
    facts = network_facts(make_inputs(networkpolicies=[deny, allow], namespaces=[ns("shop")]))
    keys = {f.key for f in facts}
    assert "network/default-deny-ingress" not in keys
    assert "network/default-deny-egress" not in keys


def test_dns_rule_folds_in_its_peers():
    dns = {
        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "kube-system"}}}],
        "ports": [{"port": 53, "protocol": "UDP"}, {"port": 53, "protocol": "TCP"}],
    }
    pol = netpol("shop", {}, ["Egress"], egress=[dns])
    f = by_key(network_facts(make_inputs(networkpolicies=[pol])), "network/default-deny-egress")
    assert f.statement == "Outbound pod traffic is denied by default in 1 namespace, except DNS"


def test_unrenderable_peers_become_other_destinations():
    pol = netpol("shop", {}, ["Egress"], egress=[{"to": [{"podSelector": {"matchLabels": {"app": "db"}}}]}])
    f = by_key(network_facts(make_inputs(networkpolicies=[pol])), "network/default-deny-egress")
    assert f.statement.endswith("except other destinations")
    assert f.certainty == "unsure"


def test_namespaces_with_different_exceptions_are_asked():
    a = netpol("a", {}, ["Egress"], egress=[{"to": [{"ipBlock": {"cidr": "10.0.0.0/8"}}]}])
    b = netpol("b", {}, ["Egress"], egress=[])
    f = by_key(network_facts(make_inputs(networkpolicies=[a, b])), "network/default-deny-egress")
    assert f.statement == "Outbound pod traffic is denied by default in 2 namespaces, each with its own exceptions"
    assert f.question == (
        "Outbound traffic is denied by default in 2 namespaces, each with its own exceptions. "
        "Which destinations are allowed?"
    )


def test_no_policies_is_unsure_when_a_policy_crd_exists():
    inputs = make_inputs(
        networkpolicies=[], crds=[crd("cilium.io", "ciliumnetworkpolicies.cilium.io")], **{"cilium-policies": []}
    )
    assert by_key(network_facts(inputs), "network/no-network-policies").certainty == "unsure"
