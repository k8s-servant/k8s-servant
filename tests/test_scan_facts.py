"""Tests for scan/facts.py: facts, their certainty and scope, and the topic order."""

import json

from k8s_servant.scan.facts import SECTION_TITLES, TOPICS, Fact, apply_scope, fact


def test_fact_defaults_to_seen_and_the_topic_scope():
    f = fact("component/kyverno", "components", "Kyverno runs.", ("CRD group kyverno.io",), ("crds",))
    assert (f.certainty, f.scope) == ("seen", "platform")


def test_a_question_makes_a_fact_unsure():
    f = fact("nodepool/user", "platform", "Node pool user: 3 nodes", (), ("nodes",), unsure_question="Correct?")
    assert (f.certainty, f.scope, f.question) == ("unsure", "cluster", "Correct?")


def test_fact_round_trips_through_a_dict():
    f = fact(
        "monitoring/prometheus-url",
        "monitoring",
        "s",
        ("e",),
        ("services",),
        scope="cluster",
        setting=("PROMETHEUS_URL", "http://p.monitoring.svc:9090"),
        setting_reason="r",
    )
    assert Fact.from_dict(json.loads(json.dumps(f.to_dict()))) == f


def test_not_representative_puts_everything_in_the_cluster_scope():
    f = fact("component/kyverno", "components", "s", (), ("crds",))
    assert apply_scope([f], representative=False)[0].scope == "cluster"
    assert apply_scope([f], representative=True)[0].scope == "platform"


def test_section_titles_follow_the_topic_order():
    assert list(SECTION_TITLES) == list(TOPICS)
    assert SECTION_TITLES["noise"] == "What is normal here"
