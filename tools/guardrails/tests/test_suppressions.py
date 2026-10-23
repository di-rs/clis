"""Regression tests for source policy boundaries."""

import pytest
from guardrails.scripts import script
from guardrails.suppressions import compare_exceptions


@pytest.mark.parametrize(
    "path,code",
    [
        (
            "a.rs",
            '#[allow(clippy::unwrap_used, reason = "constant is validated above")]\nfn f() {}',
        ),
        (
            "a.py",
            "# reason: external library provides no stubs\nimport vendor  # type: ignore[import-untyped]\n",
        ),
        (
            "a.py",
            '@pytest.mark.skipif(sys.platform == "win32", reason="requires POSIX file permissions")\ndef test_f(): pass\n',
        ),
        (
            "a.ts",
            "// reason: upstream declarations omit this property\n// @ts-expect-error\nconst x = vendor.prop;",
        ),
    ],
)
def test_narrow_documented_exceptions_pass(path: str, code: str) -> None:
    failures, additions, removals = compare_exceptions(path, "", code)
    assert failures == []
    assert additions >= 1 and removals == 0


@pytest.mark.parametrize(
    "path,code",
    [
        ("a.rs", "#![allow(unused)]\nfn f() {}"),
        ("a.py", "import vendor  # noqa\n"),
        ("a.py", "@pytest.mark.skip\ndef test_f(): pass\n"),
        ("a.ts", "// reason: this file has legacy declarations\n// @ts-nocheck\nconst x = 1;"),
    ],
)
def test_blanket_and_undocumented_exceptions_fail(path: str, code: str) -> None:
    assert compare_exceptions(path, "", code)[0]


def test_strings_are_not_suppression_directives() -> None:
    assert compare_exceptions("a.py", "", 'example = "# noqa"\n')[1] == 0


def test_existing_exceptions_are_not_retroactively_rejected() -> None:
    before = "import vendor  # type: ignore\n"
    assert compare_exceptions("a.py", before, "\n" + before)[0] == []
    assert compare_exceptions("a.py", before, "")[2] == 1


def test_unrelated_string_is_not_an_adjacent_reason() -> None:
    code = 'text = "reason: unrelated example is not justification"\nimport vendor  # noqa: F401\n'
    assert compare_exceptions("a.py", "", code)[0]


def test_cfg_attr_without_suppression_is_not_an_exception() -> None:
    assert (
        compare_exceptions("a.rs", "", "#[cfg_attr(unix, derive(Debug))]\nstruct Example;")[1] == 0
    )


def test_parametrized_examples_are_not_suppressions() -> None:
    code = '@pytest.mark.parametrize("code", ["# noqa", "@pytest.mark.skip"])\ndef test_example(code): pass\n'
    assert compare_exceptions("a.py", "", code)[1] == 0


def test_native_rust_ignore_reason_is_accepted() -> None:
    code = '#[ignore = "requires a running external service"]\n#[test]\nfn external() {}'
    assert compare_exceptions("a.rs", "", code)[0] == []


def test_conditional_crate_wide_suppression_is_rejected() -> None:
    code = "#![cfg_attr(unix, allow(dead_code))]\nfn f() {}"
    assert compare_exceptions("a.rs", "", code)[0]


def test_uppercase_svelte_script_preserves_lines() -> None:
    text = '<SCRIPT lang="ts">\nconst x = 1;\n</SCRIPT>\n<p>markup</p>'
    extracted = script("component.svelte", text)
    assert extracted.splitlines()[1] == "const x = 1;"
    assert "markup" not in extracted
