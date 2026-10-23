"""Vulnerability failures must remain distinct from unavailable services."""

from guardrails.dependency_audits import verdict


def test_audits_distinguish_failure_from_unavailable() -> None:
    assert verdict("python", {"dependencies": [{"vulns": [{"id": "CVE-test"}]}]}, 1) == (
        "vulnerable",
        1,
    )
    assert verdict("python", {"dependencies": [{"vulns": []}]}, 0) == ("clean", 0)
    assert (
        verdict("python", {"dependencies": [{"skip_reason": "unresolved"}]}, 0)[0] == "incomplete"
    )
    assert verdict("frontend", {"metadata": {"vulnerabilities": {"low": 1}}}, 1) == (
        "vulnerable",
        1,
    )
    assert verdict("frontend", {"error": "registry unavailable"}, 1)[0] == "incomplete"
    assert verdict("python", None, 1)[0] == "incomplete"


def test_known_vulnerability_still_fails_a_partially_incomplete_audit() -> None:
    assert verdict(
        "python",
        {"dependencies": [{"vulns": [{"id": "CVE-test"}]}, {"skip_reason": "unsupported"}]},
        1,
    ) == ("vulnerable", 1)
