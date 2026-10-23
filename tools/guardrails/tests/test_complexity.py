"""Regression tests for source policy boundaries."""

import pytest
from guardrails.complexity import compare_functions
from guardrails.scripts import script
from guardrails.syntax import functions


@pytest.mark.parametrize(
    "path,code",
    [
        ("a.py", "def f(x):\n    if x:\n        while x:\n            pass\n"),
        ("a.rs", "fn f(x: bool) { if x { while x { work(); } } }"),
        ("a.ts", "const f = (x: boolean) => { if (x) { while (x) { work(); } } }"),
        (
            "a.svelte",
            '<script lang="ts">\nfunction f(x: boolean) { if(x) { while(x) {} } }\n</script><p>Hello</p>',
        ),
    ],
)
def test_metrics_ignore_comments_and_strings(path: str, code: str) -> None:
    function = functions(path, code)[0]
    assert function.complexity == 3
    assert function.depth == 2
    assert not function.test


def test_function_length_excludes_blank_and_comment_lines() -> None:
    code = "def f():\n    # if while else\n\n    return 'if while else'\n"
    assert functions("a.py", code)[0].length == 2
    assert functions("a.py", code)[0].complexity == 1


def test_nesting_limit_and_test_advisory() -> None:
    code = (
        "def f(x):\n"
        + "".join("    " * i + "if x:\n" for i in range(1, 6))
        + "                        pass\n"
    )
    failures, reports = compare_functions("app.py", "", code)
    assert any("depth=5" in failure for failure in failures)
    assert reports == []
    failures, reports = compare_functions("tests/test_app.py", "", code)
    assert failures == [] and reports


def test_existing_over_limit_function_cannot_worsen_other_metrics() -> None:
    before = "def f(x):\n" + "    x += 1\n" * 101
    assert compare_functions("a.py", before, before)[0] == []
    assert compare_functions("a.py", before, before + "    x += 1\n")[0]
    assert compare_functions("a.py", before, "def f(x):\n    return x\n")[0] == []


def test_large_syntax_tree_regression() -> None:
    # The newer native binding crashed under Python 3.15 after many node accesses.
    code = "\n".join(f"fn function_{i}(x: bool) {{ if x {{ work(); }} }}" for i in range(250))
    result = functions("a.rs", code)
    assert len(result) == 250
    assert all(function.complexity == 2 for function in result)


def test_comprehensions_and_ternaries_count_as_decisions() -> None:
    assert functions("a.py", "def f(xs):\n    return [x for x in xs if x]\n")[0].complexity == 3
    assert functions("a.ts", "function f(x: boolean) { return x ? 1 : 0; }")[0].complexity == 2


def test_uppercase_svelte_script_preserves_lines() -> None:
    text = '<SCRIPT lang="ts">\nconst x = 1;\n</SCRIPT>\n<p>markup</p>'
    extracted = script("component.svelte", text)
    assert extracted.splitlines()[1] == "const x = 1;"
    assert "markup" not in extracted
