"""Exercise selection, policy boundaries and the real command in isolated Git history."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from guardrails.audits import verdict
from guardrails.changes import selection
from guardrails.policy import compare_exceptions, compare_functions
from guardrails.syntax import functions

ROOT = Path(__file__).resolve().parents[3]


def test_docs_only_does_not_select_language_checks() -> None:
    selected = selection(["README.md"], ["README.md", "src/lib.rs", "a.py", "ui.ts", "uv.lock"])
    assert selected["markdown"] and selected["spelling"]
    assert not any(selected[k] for k in ("rust", "python", "frontend", "source", "python_audit"))


def test_dependency_and_policy_changes_select_consumers() -> None:
    present = [
        "src/lib.rs",
        "a.py",
        "a.sh",
        "a.yml",
        "README.md",
        "uv.lock",
        "ui.ts",
        "pnpm-lock.yaml",
    ]
    assert selection(["Cargo.lock"], present)["rust"]
    assert selection(["uv.lock"], present)["python_audit"]
    assert selection(["pnpm-lock.yaml"], present)["frontend_audit"]
    selected = selection(["tools/guardrails/policy.py"], present)
    assert all(selected[k] for k in ("rust", "python", "source", "shell", "yaml", "markdown"))
    assert not selection(["tools/guardrails/policy.py"], ["README.md"])["rust"]


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


def test_real_command_reports_size_and_fails_policy(tmp_path: Path) -> None:
    local = subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).splitlines()
    env = {key: val for key, val in os.environ.items() if key not in local}
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", PYTHONPATH=str(ROOT / "tools")
    )

    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], cwd=tmp_path, env=env, text=True)

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Guardrail test")
    (tmp_path / "a.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD").strip()
    (tmp_path / "a.py").write_text("import missing  # noqa\n")
    (tmp_path / "uv.lock").write_text("ignored\n" * 300)
    git("add", ".")
    git("commit", "-qm", "head")

    def run(mode: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "guardrails", mode, "--base", base],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )

    size = run("size")
    assert size.returncode == 0 and "**2 / 200**" in size.stdout
    policy = run("policy")
    assert policy.returncode == 1 and "blanket suppression" in policy.stdout
    selected = json.loads(run("select").stdout)
    assert selected["python"] and not selected["rust"]


def test_workflows_scope_jobs_and_keep_writes_separate() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/guardrails.yml").read_text())
    for name, job in workflow["jobs"].items():
        if name != "select":
            assert job["needs"] == "select"
            assert "needs.select.outputs." in job["if"] or name == "size"
    assert workflow["permissions"] == {"contents": "read"}
    labels = (ROOT / ".github/workflows/guardrails-labels.yml").read_text()
    assert "pull_request_target" in labels and "actions/checkout" not in labels
    assert "--no-verification" in (ROOT / ".github/workflows/guardrails.yml").read_text()


def test_large_syntax_tree_regression() -> None:
    # The newer native binding crashed under Python 3.15 after many node accesses.
    code = "\n".join(f"fn function_{i}(x: bool) {{ if x {{ work(); }} }}" for i in range(250))
    result = functions("a.rs", code)
    assert len(result) == 250
    assert all(function.complexity == 2 for function in result)


def test_unrelated_string_is_not_an_adjacent_reason() -> None:
    code = 'text = "reason: unrelated example is not justification"\nimport vendor  # noqa: F401\n'
    assert compare_exceptions("a.py", "", code)[0]


def test_known_vulnerability_still_fails_a_partially_incomplete_audit() -> None:
    assert verdict(
        "python",
        {"dependencies": [{"vulns": [{"id": "CVE-test"}]}, {"skip_reason": "unsupported"}]},
        1,
    ) == ("vulnerable", 1)


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


def test_coverage_counts_changed_executable_lines_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from guardrails import reports

    monkeypatch.setattr(reports, "changes", lambda base, head: {"src/lib.rs": [3, 4, 5]})
    report = tmp_path / "coverage.info"
    report.write_text(
        "SF:src/lib.rs\nDA:1,5\nDA:3,0\nDA:4,1\nend_of_record\nSF:src/lib.rs\nDA:4,1\nend_of_record\n"
    )
    reports.coverage("base", "head", report)
    assert "1/2: **50.0%**" in capsys.readouterr().out


def test_conditional_crate_wide_suppression_is_rejected() -> None:
    code = "#![cfg_attr(unix, allow(dead_code))]\nfn f() {}"
    assert compare_exceptions("a.rs", "", code)[0]


def test_comprehensions_and_ternaries_count_as_decisions() -> None:
    assert functions("a.py", "def f(xs):\n    return [x for x in xs if x]\n")[0].complexity == 3
    assert functions("a.ts", "function f(x: boolean) { return x ? 1 : 0; }")[0].complexity == 2
