from k8s_servant.config import DEFAULT_KNOWLEDGE_DIR
from k8s_servant.knowledge import load_knowledge_dir

EXPECTED = {
    "cluster-health-check",
    "workload-troubleshooting",
    "node-troubleshooting",
    "flux-gitops-status",
    "prometheus-queries",
}

# Built-in skills must stay read-only and site-neutral (universal agent).
FORBIDDEN = (
    "kubectl delete",
    "kubectl apply",
    "kubectl edit",
    "kubectl patch",
    "kubectl exec",
    "kubectl drain",
    "kubectl cordon",
    "azurecr.io",
    "my-cluster",
    "example.com",
)


def test_builtin_skills_load_with_descriptions():
    skills = load_knowledge_dir(DEFAULT_KNOWLEDGE_DIR).skills
    assert set(skills) == EXPECTED
    for skill in skills.values():
        assert skill.description, skill.name
        assert len(skill.description) <= 1024, skill.name
        assert skill.body.startswith("# "), skill.name


def test_builtin_skills_are_read_only_and_generic():
    for skill in load_knowledge_dir(DEFAULT_KNOWLEDGE_DIR).skills.values():
        text = skill.body.lower()
        for word in FORBIDDEN:
            assert word not in text, f"{skill.name} mentions {word!r}"


def test_health_check_starts_with_the_summary_tools_and_accounts_for_every_alert():
    body = load_knowledge_dir(DEFAULT_KNOWLEDGE_DIR).skills["cluster-health-check"].body
    assert "cluster_problems" in body and "warning_events" in body
    assert "Every firing alert" in body
    assert "PodDisruptionBudget" in body and "ResourceQuota" in body
