"""Binary-size selection, collection, rendering and trusted publication contracts."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import collect_sizes
import pytest
import report
import yaml
from cli_check.workspace import discover
from conftest import (
    BASE,
    REPO,
    SHA,
    TOOLS,
    FakeGitHub,
    Repository,
    archive,
    fixture_executable,
    job,
)
from size_data import BinarySize, Build, SizeReport
from size_report import JOB, delta, render_size


def snapshot(root: Path, names: tuple[str, ...] = ("first", "second")) -> None:
    root.mkdir()
    (root / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["packages/*", "utils/*"]\nresolver = "3"\n'
    )
    for name in (*names, "helper"):
        path = root / ("utils" if name == "helper" else "packages") / name
        (path / "src").mkdir(parents=True)
        (path / "Cargo.toml").write_text(
            f'[package]\nname = "{name}"\nversion = "0.1.0"\nedition = "2024"\n'
            + (
                '[dependencies]\nhelper = { path = "../../utils/helper" }\n'
                if name == "first"
                else ""
            )
        )
        (path / "src" / ("lib.rs" if name == "helper" else "main.rs")).write_text(
            "pub fn helper() {}\n" if name == "helper" else "fn main() {}\n"
        )
    (root / "Cargo.lock").write_text(
        "version = 4\n"
        + "".join(
            f'[[package]]\nname = "{name}"\nversion = "0.1.0"\n'
            + ('dependencies = ["helper"]\n' if name == "first" else "")
            for name in (*names, "helper")
        )
    )


@pytest.mark.parametrize(
    ("paths", "expected"),
    [
        (["packages/first/src/main.rs"], {"first"}),
        (["packages/first/build.rs"], {"first"}),
        (["packages/first/Cargo.toml"], {"first"}),
        (["utils/helper/src/lib.rs"], {"first"}),
        (["packages/first/tests/cli.rs", "packages/first/README.md"], set()),
        (["tools/cli-bench/README.md", "tools/cli-bench/tests/test_sizes.py"], set()),
        (["tools/cli-bench/collect_sizes.py"], {"first", "second"}),
        (["Cargo.toml"], {"first", "second"}),
        ([".cargo/config.toml"], {"first", "second"}),
        (["rust-toolchain.toml"], {"first", "second"}),
        ([".github/workflows/cli-size.yml"], {"first", "second"}),
    ],
)
def test_selection_without_benchmark_suites(
    tmp_path: Path, paths: list[str], expected: set[str]
) -> None:
    before, after = tmp_path / "base", tmp_path / "head"
    snapshot(before)
    snapshot(after)
    assert collect_sizes.selected_packages(before, after, paths) == expected


def test_added_removed_and_lock_changes_select_both_revisions(tmp_path: Path) -> None:
    before, after = tmp_path / "base", tmp_path / "head"
    snapshot(before)
    snapshot(after, ("first", "added"))
    assert collect_sizes.selected_packages(before, after, ["Cargo.lock"]) == {"second", "added"}
    manifest = after / "utils/helper/Cargo.toml"
    manifest.write_text(manifest.read_text().replace("0.1.0", "0.2.0"))
    lock = after / "Cargo.lock"
    lock.write_text(
        lock.read_text().replace(
            'name = "helper"\nversion = "0.1.0"', 'name = "helper"\nversion = "0.2.0"'
        )
    )
    assert collect_sizes.selected_packages(before, after, ["Cargo.lock"]) == {
        "first",
        "second",
        "added",
    }


@pytest.mark.parametrize("failed", [False, True])
def test_collector_uses_cargo_executable_and_rejects_partial_build_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: bool
) -> None:
    root = tmp_path / "workspace"
    snapshot(root)
    members = discover(root)
    cargo = fixture_executable(
        tmp_path,
        "cargo",
        """
from pathlib import Path
args = sys.argv[1:]
assert all(flag in args for flag in ("--locked", "--release", "--bins"))
name = args[args.index("-p") + 1]
out = Path(args[args.index("--target-dir") + 1]) / "custom" / name
out.parent.mkdir(parents=True, exist_ok=True)
out.write_bytes(b"1234567")
print(json.dumps({"reason": "compiler-artifact", "target": {"name": name, "kind": ["bin"]}, "executable": str(out)}))
sys.exit(int(os.environ["FAIL_BUILD"]))
""",
    )
    monkeypatch.setenv("PATH", str(cargo.parent) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("FAIL_BUILD", "1" if failed else "0")
    sizes = collect_sizes.build_sizes(root, members, {"first"}, tmp_path / "target")
    assert sizes == {
        ("first", "first"): Build(status="failed") if failed else Build(status="complete", bytes=7)
    }


def size_document() -> SizeReport:
    return SizeReport(
        base_sha="b" * 40,
        head_sha=SHA,
        run_attempt=1,
        toolchain="nightly-2026-10-04",
        target="x86_64-unknown-linux-gnu",
        rows=[
            BinarySize(
                package="first",
                binary="first",
                base=Build(status="complete", bytes=100),
                head=Build(status="complete", bytes=125),
            )
        ],
    )


@pytest.fixture
def size_github(github: FakeGitHub) -> FakeGitHub:
    github.run["path"] = ".github/workflows/cli-size.yml"
    github.responses[f"{BASE}/actions/workflows/cli-size.yml"] = {
        "id": 42,
        "path": github.run["path"],
    }
    github.jobs[:] = [job(JOB, "00:00", "03:10")]
    github.responses[f"{BASE}/actions/runs/500/artifacts?per_page=100&page=1"] = {
        "artifacts": [
            {
                "id": 1,
                "name": "cli-size-linux",
                "size_in_bytes": 1000,
                "created_at": "2026-10-13T00:03:00Z",
            }
        ]
    }
    github.responses[f"{BASE}/actions/artifacts/1/zip"] = archive(
        {"sizes.json": size_document().model_dump()}
    )
    return github


def test_size_totals_added_removed_unchanged_and_failed(github: FakeGitHub) -> None:
    document = size_document()
    document.rows += [
        BinarySize(
            package="new",
            binary="new",
            base=Build(status="absent"),
            head=Build(status="complete", bytes=50),
        ),
        BinarySize(
            package="gone",
            binary="gone",
            base=Build(status="complete", bytes=20),
            head=Build(status="absent"),
        ),
        BinarySize(
            package="same",
            binary="same",
            base=Build(status="complete", bytes=10),
            head=Build(status="complete", bytes=10),
        ),
    ]
    rendered = render_size(REPO, github.run, document)
    assert "Affected-set total:** 185 B (base: 130 B)" in rendered
    assert "+55 B · +42.31%" in rendered
    assert "Added" in rendered and "Removed" in rendered
    assert "Unchanged binaries</strong> (1)" in rendered
    assert delta(0, 50) == "+50 B | —"
    assert delta(100, 0) == "-100 B | -100.00%"
    document.rows[0].head = Build(status="failed")
    rendered = render_size(REPO, github.run, document)
    assert "**Incomplete:**" in rendered and "Affected-set total" not in rendered
    assert "| `first` | `first` | 100 B | — | — | — | Unavailable |" in rendered


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": 2},
        {"head_sha": "c" * 40},
        {"run_attempt": 2},
        {"toolchain": "@everyone"},
        {"target": "aarch64-apple-darwin"},
        {
            "rows": [
                {
                    "package": "first",
                    "binary": "first",
                    "base": {"status": "complete", "bytes": True},
                    "head": {"status": "absent"},
                }
            ]
        },
    ],
)
def test_invalid_size_artifacts_are_visible_without_untrusted_text(
    size_github: FakeGitHub, change: dict[str, object]
) -> None:
    size_github.responses[f"{BASE}/actions/artifacts/1/zip"] = archive(
        {"sizes.json": size_document().model_dump() | change}
    )
    result = report.generate(size_github, REPO, 500, kind="size")
    assert result is not None and "artifact unavailable or invalid" in result
    assert "@everyone" not in result


def test_size_publication_is_sticky_owned_and_ordered(size_github: FakeGitHub) -> None:
    github = size_github
    github.comments.append(
        {
            "id": 1,
            "body": "<!-- cli-size-report number=10 run=500 attempt=1 -->",
            "user": {"login": "other"},
        }
    )
    report.generate(github, REPO, 500, kind="size", author="bot")
    report.generate(github, REPO, 500, kind="size", author="bot")
    assert len(github.comments) == 2
    assert "125 B" in str(github.comments[1]["body"])
    newer = str(github.comments[1]["body"]).replace("attempt=1", "attempt=2")
    github.comments[1]["body"] = newer
    report.generate(github, REPO, 500, kind="size", author="bot")
    assert github.comments[1]["body"] == newer
    github.pull["head"] = {"sha": "c" * 40}
    assert report.generate(github, REPO, 500, kind="size", author="bot") is None


def test_size_rejects_previous_attempt_archive(size_github: FakeGitHub) -> None:
    size_github.jobs[0]["started_at"] = "2026-10-13T00:04:00Z"
    result = report.generate(size_github, REPO, 500, kind="size")
    assert result is not None and "artifact unavailable or invalid" in result


def test_size_failure_without_artifact_and_empty_selection(size_github: FakeGitHub) -> None:
    document = size_document()
    document.rows = []
    size_github.responses[f"{BASE}/actions/artifacts/1/zip"] = archive(
        {"sizes.json": document.model_dump()}
    )
    result = report.generate(size_github, REPO, 500, kind="size")
    assert result is not None and "No affected binary packages" in result
    size_github.run["conclusion"] = "failure"
    size_github.responses[f"{BASE}/actions/runs/500/artifacts?per_page=100&page=1"] = {
        "artifacts": []
    }
    result = report.generate(size_github, REPO, 500, kind="size")
    assert result is not None and "**Incomplete:**" in result


def test_size_workflows_preserve_publication_boundary() -> None:
    directory = TOOLS.parents[1] / ".github/workflows"
    execution = yaml.safe_load((directory / "cli-size.yml").read_text())
    publishing = yaml.safe_load((directory / "cli-size-report.yml").read_text())
    assert execution["permissions"] == {"contents": "read"}
    assert execution["jobs"]["sizes"]["runs-on"] == "ubuntu-24.04"
    assert "pull_request_target" not in execution.get("on", execution.get(True, {}))
    steps = publishing["jobs"]["report"]["steps"]
    assert "ref" not in steps[0]["with"]
    assert "--kind size" in steps[-1]["run"]
    assert "collect_sizes" not in str(steps)


@pytest.mark.integration
def test_real_collector_builds_two_revisions_and_handles_added_removed(
    repo: Repository, tmp_path: Path
) -> None:
    root = tmp_path / "real"
    snapshot(root, ("first", "removed"))
    repository = Repository(root, repo.env)
    toolchain = (TOOLS.parents[1] / "rust-toolchain.toml").read_text()
    repository.write("rust-toolchain.toml", toolchain)
    repository.git("init", "-b", "main")
    repository.git("config", "user.name", "Size Test")
    repository.git("config", "user.email", "size@example.invalid")
    base = repository.commit()
    shutil.copytree(root / "packages/removed", root / "packages/added")
    manifest = root / "packages/added/Cargo.toml"
    manifest.write_text(manifest.read_text().replace('name = "removed"', 'name = "added"'))
    shutil.rmtree(root / "packages/removed")
    repository.write("packages/first/src/main.rs", 'fn main() { println!("changed"); }\n')
    lock = root / "Cargo.lock"
    lock.write_text(lock.read_text().replace('name = "removed"', 'name = "added"'))
    head = repository.commit()
    output = tmp_path / "sizes.json"
    result = subprocess.run(
        [
            sys.executable,
            str(TOOLS / "collect_sizes.py"),
            "--root",
            str(root),
            "--base",
            base,
            "--head",
            head,
            "--output",
            str(output),
        ],
        env=repo.env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    document = SizeReport.model_validate_json(output.read_bytes())
    first = next(row for row in document.rows if row.package == "first")
    assert first.base.status == first.head.status == "complete"
    assert first.base.bytes and first.head.bytes
    removed = next(row for row in document.rows if row.package == "removed")
    assert removed.base.status == "complete" and removed.head.status == "absent"
    added = next(row for row in document.rows if row.package == "added")
    assert added.base.status == "absent" and added.head.status == "complete"


def test_long_size_report_bounds_comment_but_keeps_full_totals(github: FakeGitHub) -> None:
    document = size_document()
    document.rows = [
        BinarySize(
            package=f"package-{i:03}-" + "a" * 60,
            binary="b" * 80,
            base=Build(status="complete", bytes=10**12),
            head=Build(status="complete", bytes=10**12 - 1),
        )
        for i in range(256)
    ]
    result = render_size(REPO, github.run, document)
    assert "Showing 120 of 256 binaries" in result
    assert "255,999,999,999,744 B" in result
    assert len(result) < 65536


@pytest.mark.parametrize(
    "change",
    [
        {"path": ".github/workflows/other.yml"},
        {"workflow_id": 43},
        {"event": "push"},
        {"conclusion": "cancelled"},
    ],
)
def test_size_wrong_workflow_or_cancelled_run_never_posts(
    size_github: FakeGitHub, change: dict[str, object]
) -> None:
    size_github.run.update(change)
    assert report.generate(size_github, REPO, 500, kind="size", author="bot") is None
    assert not size_github.comments


@pytest.mark.parametrize(
    "content",
    [
        {"../sizes.json": {}},
        {"sizes.json": "x" * (report.MAX_MEMBER + 1)},
    ],
)
def test_size_archive_limits_are_enforced(
    size_github: FakeGitHub, content: dict[str, object]
) -> None:
    size_github.responses[f"{BASE}/actions/artifacts/1/zip"] = archive(content)
    result = report.generate(size_github, REPO, 500, kind="size")
    assert result is not None and "artifact unavailable or invalid" in result
