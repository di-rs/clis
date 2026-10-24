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


def test_rust_method_identity_survives_impl_reordering() -> None:
    long_body = "work();\n" * 101
    first = f"impl First {{ fn validate() {{ {long_body} }} }}"
    second = "impl Second { fn validate() { work(); } }"
    before = first + second
    assert compare_functions("app.rs", before, second + first)[0] == []


@pytest.mark.parametrize(
    "old_impl,new_impl",
    [
        ("First", "Second"),
        ("First", "Trait for First"),
        ("One for First", "Two for First"),
        ("Trait<u8> for First", "Trait<u16> for First"),
        ("First<u8>", "First<u16>"),
    ],
)
def test_new_rust_method_cannot_inherit_unrelated_allowance(old_impl: str, new_impl: str) -> None:
    body = "work();\n" * 101
    old = f"impl {old_impl} {{ fn validate() {{ {body} }} }}"
    new = f"impl {new_impl} {{ fn validate() {{ {body} }} }}"
    failures, reports = compare_functions("app.rs", old, new)
    assert len(failures) == 1
    assert "length=" in failures[0]
    assert reports == []


@pytest.mark.parametrize("path", ["app.rs", "app.js", "app.ts", "app.tsx"])
@pytest.mark.parametrize("nested", ["", "if (inner) { work(); }"])
def test_else_if_chain_keeps_sibling_depth(path: str, nested: str) -> None:
    signature = "fn f()" if path.endswith(".rs") else "function f()"
    chain = " else ".join(f"if (c{i}) {{ {nested} }}" for i in range(5))
    code = f"{signature} {{ {chain} else {{ work(); }} }}"
    function = functions(path, code)[0]
    assert function.depth == (2 if nested else 1)
    assert function.complexity == (11 if nested else 6)
    assert compare_functions(path, "", code) == ([], [])


@pytest.mark.parametrize("path", ["app.rs", "app.js", "app.ts"])
def test_if_inside_else_block_is_nested(path: str) -> None:
    signature = "fn f()" if path.endswith(".rs") else "function f()"
    code = f"{signature} {{ if (a) {{}} else if (b) {{}} else {{ if (c) {{}} }} }}"
    function = functions(path, code)[0]
    assert function.depth == 2
    assert function.complexity == 4
