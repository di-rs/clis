"""Temporary repositories and external-tool fixtures for CLI benchmark tests."""

import io
import json
import os
import shutil
import subprocess
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import NotRequired, TypedDict, cast

import pytest
import report
from select_suites import Selection

TOOLS = Path(__file__).resolve().parents[1]


@dataclass
class Repository:
    root: Path
    env: dict[str, str]
    base: str = ""

    def write(self, path: str, content: str) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def write_suite(self, directory: str, binary: str, *, generate: bool = False) -> None:
        self.write(
            f"{directory}/benches/cli-bench.toml",
            f'binary = "{binary}"\n'
            + ('generate = [["text", "input.txt"]]\n' if generate else "generate = []\n")
            + '[[cases]]\nname = "sample"\nargs = []\n',
        )

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=self.root, env=self.env, text=True, capture_output=True, check=True
        ).stdout.strip()

    def commit(self) -> str:
        self.git("add", ".")
        self.git("commit", "--quiet", "--allow-empty", "-m", "fixture")
        return self.git("rev-parse", "HEAD")

    def run_selector(
        self, body: str | None = None, *, previous_body: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        event: dict[str, object] = {"pull_request": {"body": body}}
        if previous_body is not None:
            event |= {"action": "edited", "changes": {"body": {"from": previous_body}}}
        event_path = self.root / "event.json"
        event_path.write_text(json.dumps(event))
        return subprocess.run(
            [
                sys.executable,
                str(self.root / "tools/cli-bench/select_suites.py"),
                "--base",
                self.base,
                "--head",
                self.git("rev-parse", "HEAD"),
                "--event",
                str(event_path),
            ],
            cwd=self.root.parent,
            env=self.env | {"GITHUB_STEP_SUMMARY": str(self.root / "summary.md")},
            capture_output=True,
            text=True,
        )

    def selection(self, body: str | None = None, *, previous_body: str | None = None) -> Selection:
        result = self.run_selector(body, previous_body=previous_body)
        assert result.returncode == 0, result.stderr
        selected = cast(Selection, json.loads(result.stdout))
        assert isinstance(selected["reason"], str) and selected["reason"]
        return selected


@pytest.fixture
def repo(tmp_path: Path) -> Repository:
    # Hooks export the parent repository's index/worktree; fixtures must not inherit them.
    git_local_env = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    env = {key: value for key, value in os.environ.items() if key not in git_local_env} | {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    repository = Repository(tmp_path, env)
    script = tmp_path / "tools/cli-bench/select_suites.py"
    script.parent.mkdir(parents=True)
    shutil.copyfile(TOOLS / "select_suites.py", script)
    repository.write(
        "Cargo.toml",
        '[workspace]\nmembers = ["biggie", "coreutils/*", "utils/*"]\n'
        'exclude = ["coreutils/excluded"]\n',
    )
    for name in ("biggie", "echor", "tailr", "wcr", "excluded"):
        directory = "biggie" if name == "biggie" else f"coreutils/{name}"
        repository.write(f"{directory}/Cargo.toml", f'[package]\nname = "{name}"\n')
        repository.write(f"{directory}/src/main.rs", "fn main() {}\n")
        repository.write_suite(directory, name, generate=name == "tailr")
    repository.write("utils/cli-log/Cargo.toml", '[package]\nname = "cli-log"\n')
    repository.write("utils/cli-log/src/lib.rs", "// logging\n")
    repository.write("outside/benches/cli-bench.toml", 'binary = "outside"\n')
    repository.git("init", "-b", "main")
    repository.git("config", "user.name", "Selector Tests")
    repository.git("config", "user.email", "selector@example.invalid")
    repository.base = repository.commit()
    return repository


RUNNER = Path(__file__).resolve().parents[1] / "bench.py"


class Event(TypedDict):
    cwd: str
    generate: NotRequired[list[str]]
    hyperfine: NotRequired[list[str]]
    argv: NotRequired[list[str]]
    locale: NotRequired[str]
    logging: NotRequired[list[str]]


def fixture_executable(root: Path, name: str, body: str, version: str = "fixture 1.0") -> Path:
    path = root / name
    path.write_text(
        f"#!{sys.executable}\nimport json, os, sys\n"
        f"if sys.argv[1:] == ['--version']:\n    print({version!r})\n    sys.exit(0)\n"
        + body
        + "\n"
    )
    path.chmod(0o755)
    return path


@dataclass
class Bench:
    root: Path
    suite: Path
    output: Path
    trace: Path
    env: dict[str, str]
    candidate: Path
    baseline: Path
    biggie: Path
    hyperfine: Path

    def executable(self, name: str, body: str, version: str = "fixture 1.0") -> Path:
        return fixture_executable(self.root, name, body, version)

    def write_suite(self, case: str = 'name = "sample"\nargs = []', top: str = "") -> None:
        self.suite.write_text('binary = "fixture"\n' + top + "\n[[cases]]\n" + case + "\n")

    def run_bench(self, *args: str, compare: bool = True) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(RUNNER),
            str(self.suite),
            "--candidate",
            str(self.candidate),
            "--biggie",
            str(self.biggie),
            "--hyperfine",
            str(self.hyperfine),
            "--output",
            str(self.output),
            "--runs",
            "2",
            "--warmup",
            "1",
        ]
        if compare:
            command += ["--baseline", str(self.baseline)]
        return subprocess.run(command + list(args), env=self.env, capture_output=True, text=True)

    def events(self) -> list[Event]:
        return (
            [cast(Event, json.loads(line)) for line in self.trace.read_text().splitlines()]
            if self.trace.exists()
            else []
        )


@pytest.fixture
def bench(tmp_path: Path) -> Bench:
    root = tmp_path.resolve()
    trace = root / "trace.jsonl"
    return Bench(
        root=root,
        suite=root / "suite.toml",
        output=root / "results",
        trace=trace,
        env=os.environ | {"TRACE": str(trace)},
        candidate=fixture_executable(root, "candidate 'quoted'", "print('same')"),
        baseline=fixture_executable(root, "baseline", "print('same')"),
        biggie=fixture_executable(
            root,
            "biggie",
            """
import pathlib
pathlib.Path(sys.argv[1]).write_text(sys.argv[2])
with open(os.environ['TRACE'], 'a') as trace:
    trace.write(json.dumps({'generate': sys.argv[1:], 'cwd': os.getcwd(),
        'logging': [os.environ.get('CLIS_LOG_LEVEL'), os.environ.get('CLIS_LOG_STDERR')]}) + '\\n')
""",
        ),
        hyperfine=fixture_executable(
            root,
            "hyperfine",
            """
import pathlib
args = sys.argv[1:]
with open(os.environ['TRACE'], 'a') as trace:
    trace.write(json.dumps({'hyperfine': args, 'cwd': os.getcwd(),
        'logging': [os.environ.get('CLIS_LOG_LEVEL'), os.environ.get('CLIS_LOG_STDERR')]}) + '\\n')
def option(name):
    return args[args.index(name) + 1]
commands = args[args.index('--') + 1:]
runs = int(option('--runs'))
status = next((int(a.split('=')[1]) for a in args if a.startswith('--ignore-failure=')), 0)
mode = os.environ.get('FAKE_MODE', '')
if mode == 'wrong-exit':
    status = 0 if status else 1
results = [{'command': command, 'measurements': [{'exit_code': status} for _ in range(runs)],
            'summary': {'time_wall_clock': {'count': runs, 'mean': 0.001}}}
           for command in commands]
if mode == 'partial':
    results.pop()
pathlib.Path(option('--export-json')).write_text(json.dumps({'schema_version': 2, 'results': results}))
pathlib.Path(option('--export-markdown')).write_text('| Command | Mean |\\n| --- | --- |\\n')
sys.exit(1 if mode == 'failure' else 0)
""",
            version="hyperfine 2.0.0",
        ),
    )


REPO = "di-rs/clis"
SHA = "a" * 40
BASE = f"repos/{REPO}"


def archive(files: dict[str, object]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for name, value in files.items():
            target.writestr(name, value if isinstance(value, str) else json.dumps(value))
    return buffer.getvalue()


def benchmark(mean: object = 0.002, runs: int = 1) -> dict[str, object]:
    return {
        "schema_version": 2,
        "results": [
            {
                "name": name,
                "summary": {"time_wall_clock": {"unit": "second", "mean": time, "count": runs}},
            }
            for name, time in (("reference", 0.004), ("candidate", mean))
        ],
    }


def suite_files(mean: object = 0.002, runs: int = 1) -> dict[str, object]:
    return {
        "coreutils-tailr-benches-cli-bench.toml/suite.toml": 'binary = "tailr"\n',
        "coreutils-tailr-benches-cli-bench.toml/metadata.json": {
            "status": "complete",
            "cases": ["last-lines"],
            "completed_cases": ["last-lines"],
            "runs": runs,
            "warmup": 0,
            "executables": {"candidate": "/tmp/ignore", "baseline": "/usr/bin/tail"},
        },
        "coreutils-tailr-benches-cli-bench.toml/last-lines.json": benchmark(mean, runs),
        "coreutils-tailr-benches-cli-bench.toml/last-lines.md": "@everyone [ignore](evil)",
    }


def job(name: str, start: str, end: str) -> dict[str, object]:
    return {
        "name": name,
        "conclusion": "success",
        "started_at": f"2026-10-13T00:{start}Z",
        "completed_at": f"2026-10-13T00:{end}Z",
        "steps": [
            {
                "name": "Build selected CLIs",
                "started_at": "2026-10-13T00:01:00Z",
                "completed_at": "2026-10-13T00:02:00Z",
            },
            {
                "name": "Run suites",
                "started_at": "2026-10-13T00:02:00Z",
                "completed_at": "2026-10-13T00:02:30Z",
            },
        ],
    }


class FakeGitHub(report.GitHub):
    def __init__(self) -> None:
        self.run: dict[str, object] = {
            "id": 500,
            "run_number": 10,
            "run_attempt": 1,
            "workflow_id": 42,
            "name": "CLI benchmark suites",
            "path": ".github/workflows/cli-bench.yml",
            "event": "pull_request",
            "status": "completed",
            "conclusion": "success",
            "repository": {"full_name": REPO},
            "head_sha": SHA,
            "pull_requests": [{"number": 7}],
        }
        self.pull: dict[str, object] = {
            "number": 7,
            "state": "open",
            "head": {"sha": SHA},
            "base": {"repo": {"full_name": REPO}, "ref": "master"},
        }
        self.jobs = [
            job("select", "00:00", "00:10"),
            job("suites (ubuntu-24.04)", "00:10", "03:10"),
            job("suites (macos-15)", "00:20", "04:20"),
        ]
        self.comments: list[dict[str, object]] = []
        self.responses: dict[str, object] = {
            f"{BASE}/actions/runs/500": self.run,
            f"{BASE}/actions/workflows/cli-bench.yml": {
                "id": 42,
                "path": ".github/workflows/cli-bench.yml",
            },
            f"{BASE}/pulls/7": self.pull,
            f"{BASE}/commits/{SHA}/pulls?per_page=100&page=1": [{"number": 7}],
            f"{BASE}/actions/runs/500/attempts/1/jobs?per_page=100&page=1": {"jobs": self.jobs},
            f"{BASE}/issues/7/comments?per_page=100&page=1": self.comments,
            f"{BASE}/actions/runs/500/artifacts?per_page=100&page=1": {
                "artifacts": [
                    {
                        "id": 1,
                        "name": "cli-bench-ubuntu-24.04",
                        "size_in_bytes": 2000,
                        "created_at": "2026-10-13T00:03:00Z",
                    },
                    {"id": 2, "name": "cli-bench-selection", "size_in_bytes": 200},
                ]
            },
            f"{BASE}/actions/artifacts/1/zip": archive(suite_files()),
            f"{BASE}/actions/artifacts/2/zip": archive(
                {"selection.json": {"binaries": ["tailr"], "runs": 1, "warmup": 0}}
            ),
        }

    def request(
        self, path: str, *, method: str = "GET", body: object = None, limit: int = 0
    ) -> bytes:
        if method == "POST":
            self.comments.append(
                {"id": 90, "body": cast(dict[str, object], body)["body"], "user": {"login": "bot"}}
            )
            return b"{}"
        if method == "PATCH":
            comment_id = int(path.rsplit("/", 1)[1])
            comment = next(item for item in self.comments if item["id"] == comment_id)
            comment["body"] = cast(dict[str, object], body)["body"]
            return b"{}"
        value = self.responses[path]
        return value if isinstance(value, bytes) else json.dumps(value).encode()


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()
