"""Test doubles shared by several test files (imported as `from fakes import ...`)."""

import json
from dataclasses import replace
from pathlib import Path

from k8s_servant.llm.types import ToolCall, Turn
from k8s_servant.scan.inputs import INPUTS, Inputs
from k8s_servant.tools.cluster_tools import Fetch


def answer(text: str) -> Turn:
    """A model reply with text and no tool calls."""
    return Turn("assistant", text)


def tool_call(name: str, args: dict, call_id: str = "c1") -> Turn:
    """A model reply asking for one tool call."""
    return Turn("assistant", tool_calls=[ToolCall(call_id, name, json.dumps(args))])


def findings_answer(summary: str, findings: list[dict]) -> str:
    """A health-check answer ending with the findings JSON block."""
    return (
        "I checked the cluster.\n\n```json\n"
        + json.dumps({"summary": summary, "findings": findings}, indent=2)
        + "\n```\n"
    )


class ScriptedChat:
    """Fake model: returns the scripted replies in order and records every request.

    A reply that is an exception instance is raised instead of returned.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    total_tokens = 0

    async def complete(self, turns, tools, *, use_tools=True):
        self.calls.append({"turns": [replace(t) for t in turns], "tools": tools, "use_tools": use_tools})
        reply = self.replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def fixture_fetch(flavor: str, calls: list | None = None, fail: dict[str, Exception] | None = None) -> Fetch:
    """A `Fetch` that answers scan inputs from tests/fixtures/scan/<flavor>/<name>.json.

    The file name is the scan input's name (matched against INPUTS), or
    `raw-prometheus` / `raw-alertmanager` for `get --raw` service-proxy paths. A
    missing file raises RuntimeError("NotFound"); `fail[name]` is raised instead.
    """
    root = Path(__file__).parent / "fixtures" / "scan" / flavor

    async def fetch(args: list[str]) -> dict:
        if calls is not None:
            calls.append(args)
        name = next((n for n, a, _ in INPUTS if a == args), None)
        if args[:2] == ["get", "--raw"]:
            name = (
                "raw-prometheus"
                if "/api/v1/query" in args[2]
                else "raw-alertmanager"
                if "/api/v2/alerts" in args[2]
                else None
            )
        if name in (fail or {}):
            raise fail[name]
        path = root / f"{name}.json"
        if name is None or not path.exists():
            raise RuntimeError("NotFound")
        return json.loads(path.read_text())

    return fetch


def make_inputs(*, failed: dict[str, str] | None = None, **items: list[dict]) -> Inputs:
    """Scan `Inputs` built by hand from per-input item lists (and optional failures)."""
    return Inputs(items=dict(items), failed=dict(failed or {}))


def crd(group: str, name: str | None = None) -> dict:
    """A CustomResourceDefinition of an API group."""
    return {"metadata": {"name": name or f"things.{group}"}, "spec": {"group": group}}


def deploy(namespace: str, name: str, ready: int = 1) -> dict:
    """A Deployment with `ready` ready replicas."""
    return {"metadata": {"namespace": namespace, "name": name}, "status": {"readyReplicas": ready}}


def daemonset(namespace: str, name: str, ready: int = 1) -> dict:
    """A DaemonSet with `ready` ready pods."""
    return {"metadata": {"namespace": namespace, "name": name}, "status": {"numberReady": ready}}


def statefulset(namespace: str, name: str, ready: int = 1) -> dict:
    """A StatefulSet with `ready` ready replicas."""
    return {"metadata": {"namespace": namespace, "name": name}, "status": {"readyReplicas": ready}}


def webhook(name: str) -> dict:
    """A (mutating or validating) webhook configuration."""
    return {"metadata": {"name": name}}


def ns(name: str, labels: dict[str, str] | None = None) -> dict:
    """A Namespace."""
    return {"metadata": {"name": name, "labels": labels or {}}}


def by_key(facts: list, key: str):
    """The fact with this key; fails the test when there is none."""
    return next(f for f in facts if f.key == key)


def node(name: str, labels: dict[str, str] | None = None, provider_id: str = "") -> dict:
    """A Node with labels and an optional `spec.providerID`."""
    return {"metadata": {"name": name, "labels": labels or {}}, "spec": {"providerID": provider_id}}


def service(
    namespace: str,
    name: str,
    type: str = "ClusterIP",
    annotations: dict[str, str] | None = None,
    ports: list[dict] | None = None,
    cluster_ip: str | None = None,
) -> dict:
    """A Service of a type, with annotations, ports and an optional cluster IP."""
    spec: dict = {"type": type, "ports": ports or []}
    if cluster_ip:
        spec["clusterIP"] = cluster_ip
    return {"metadata": {"namespace": namespace, "name": name, "annotations": annotations or {}}, "spec": spec}


def netpol(
    namespace: str,
    pod_selector: dict,
    types: list[str],
    ingress: list[dict] | None = None,
    egress: list[dict] | None = None,
    name: str = "default-deny",
) -> dict:
    """A NetworkPolicy; a rule list of `None` leaves the key out, `[]` sets it empty."""
    spec: dict = {"podSelector": pod_selector}
    if types:
        spec["policyTypes"] = types
    if ingress is not None:
        spec["ingress"] = ingress
    if egress is not None:
        spec["egress"] = egress
    return {"metadata": {"namespace": namespace, "name": name}, "spec": spec}


def ingressclass(name: str, default: bool = False) -> dict:
    """An IngressClass, optionally the default one."""
    annotations = {"ingressclass.kubernetes.io/is-default-class": "true"} if default else {}
    return {"metadata": {"name": name, "annotations": annotations}}


def gateway(namespace: str, name: str, class_name: str) -> dict:
    """A Gateway API Gateway of a class."""
    return {"metadata": {"namespace": namespace, "name": name}, "spec": {"gatewayClassName": class_name}}
