import json

import pytest

from k8s_servant.findings import Finding, FindingsParseError, normalize_fingerprint, parse_findings, sort_findings


def _block(payload) -> str:
    return "I checked everything.\n\n```json\n" + json.dumps(payload) + "\n```\n"


def test_parse_a_valid_block():
    summary, findings = parse_findings(
        _block(
            {
                "summary": "One problem.",
                "findings": [
                    {
                        "fingerprint": "crashloop/payments-dev/Deployment/api",
                        "severity": "Warning",
                        "title": "api crashes",
                        "detail": "exit code 1",
                    }
                ],
            }
        )
    )
    assert summary == "One problem."
    assert findings == [Finding("crashloop/payments-dev/deployment/api", "warning", "api crashes", "exit code 1")]


def test_the_last_json_block_wins():
    text = (
        _block({"summary": "draft", "findings": []}) + "\nCorrected:\n" + _block({"summary": "final", "findings": []})
    )
    assert parse_findings(text)[0] == "final"


def test_bare_json_without_a_fence_is_accepted():
    assert parse_findings(json.dumps({"summary": "ok", "findings": []})) == ("ok", [])


def test_empty_findings_mean_healthy():
    assert parse_findings(_block({"summary": "All good.", "findings": []})) == ("All good.", [])


@pytest.mark.parametrize(
    "text",
    [
        "no json here at all",
        "```json\n{not json}\n```",
        "```json\n[1, 2]\n```",
        '```json\n{"summary": "x"}\n```',
        '```json\n{"findings": ["x"]}\n```',
        '```json\n{"findings": [{"title": "no fingerprint"}]}\n```',
        "",
    ],
)
def test_invalid_answers_raise(text):
    with pytest.raises(FindingsParseError):
        parse_findings(text)


def test_unknown_severity_becomes_warning_and_title_defaults_to_fingerprint():
    _, findings = parse_findings(_block({"findings": [{"fingerprint": "a/b/c/d", "severity": "urgent"}]}))
    assert findings == [Finding("a/b/c/d", "warning", "a/b/c/d", "")]


def test_duplicate_fingerprints_keep_the_most_severe():
    _, findings = parse_findings(
        _block(
            {
                "findings": [
                    {"fingerprint": "x/ns/Deployment/a", "severity": "info", "title": "minor"},
                    {"fingerprint": "X/ns/deployment/A", "severity": "critical", "title": "major"},
                ]
            }
        )
    )
    assert findings == [Finding("x/ns/deployment/a", "critical", "major", "")]


def test_normalize_fingerprint():
    assert normalize_fingerprint("  CrashLoop/Payments-Dev//Deployment/API ") == "crashloop/payments-dev/deployment/api"
    assert normalize_fingerprint("node pressure/_cluster/Node/n 1") == "node-pressure/_cluster/node/n-1"
    assert normalize_fingerprint(None) == ""


def test_sort_findings_by_severity_then_fingerprint():
    items = [Finding("b", "info", "", ""), Finding("c", "critical", "", ""), Finding("a", "info", "", "")]
    assert [f.fingerprint for f in sort_findings(items)] == ["c", "a", "b"]


def test_spaces_around_slashes_do_not_change_the_fingerprint():
    # Final review: the model may write "a / b / c / d" on one run and "a/b/c/d" on the next;
    # both must be the same finding, or Teams gets a resolved+new pair every hour.
    assert (
        normalize_fingerprint("crashloop / payments-dev / Deployment / api") == "crashloop/payments-dev/deployment/api"
    )
