"""Resolved dependency identities, cycles and invalid lockfiles."""

import pytest
from cli_check.affected import lock_closures

LOCK = """
version = 4
[[package]]
name = "cli"
version = "0.1.0"
dependencies = ["helper"]
[[package]]
name = "helper"
version = "1.0.0"
source = "registry+https://example.invalid/index"
checksum = "original"
"""


@pytest.mark.parametrize("field", ["version", "source", "checksum"])
def test_dependency_identity_or_contents_change_the_consumers_closure(field: str) -> None:
    before = lock_closures(LOCK)
    changes = {
        "version": ('version = "1.0.0"', 'version = "2.0.0"'),
        "source": (
            "registry+https://example.invalid/index",
            "git+https://example.invalid/repo#abc",
        ),
        "checksum": ('checksum = "original"', 'checksum = "updated"'),
    }
    after = lock_closures(LOCK.replace(*changes[field]))
    assert before["cli"] != after["cli"]


def test_source_qualified_dependencies_ignore_an_unrelated_source_with_the_same_version() -> None:
    before = lock_closures(LOCK)
    after = lock_closures(
        LOCK.replace('["helper"]', '["helper 1.0.0 (registry+https://example.invalid/index)"]')
        + """
[[package]]
name = "helper"
version = "1.0.0"
source = "git+https://example.invalid/repo#abc"
"""
    )
    assert before["cli"] == after["cli"]


def test_dependency_cycles_terminate_and_retain_all_packages() -> None:
    cyclic = LOCK.replace('checksum = "original"', 'dependencies = ["cli"]')
    assert {key[0] for key in lock_closures(cyclic)["cli"]} == {"cli", "helper"}


@pytest.mark.parametrize(
    "content",
    [
        LOCK.replace('["helper"]', '["missing"]'),
        LOCK + '\n[[package]]\nname = "helper"\nversion = "2.0.0"\n',
        LOCK + '\n[[package]]\nname = "cli"\nversion = "0.1.0"\n',
    ],
)
def test_unresolved_ambiguous_or_duplicate_dependencies_fail_instead_of_underselecting(
    content: str,
) -> None:
    with pytest.raises(ValueError):
        lock_closures(content)
