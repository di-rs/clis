"""Select CI jobs from a complete merge-base diff, without installing the workspace."""

import argparse
import json
import os
import subprocess
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
JOBS = ("rust", "python", "frontend", "dependencies", "codeql_rust", "codeql_actions")


def cargo_directories(root: Path) -> tuple[str, ...]:
    workspace = tomllib.loads((root / "Cargo.toml").read_text())["workspace"]
    return tuple(
        directory.relative_to(root).as_posix() + "/"
        for member in workspace["members"]
        for directory in root.glob(member)
        if (directory / "Cargo.toml").is_file()
    )


def select_jobs(paths: list[str], cargo_dirs: tuple[str, ...]) -> dict[str, bool]:
    # Changing the selection policy or shared hooks must exercise every consumer.
    full = any(
        path.startswith("tools/ci/") or path in {".github/workflows/ci-selection.yml", "prek.toml"}
        for path in paths
    )
    validation = full or ".github/workflows/pull-request-check.yml" in paths
    rust = validation or any(
        path.startswith((*cargo_dirs, ".cargo/", ".config/nextest", "tests/consumers/"))
        or path.endswith((".rs", "/Cargo.toml", "/Cargo.lock"))
        or path in {"Cargo.toml", "Cargo.lock", "rust-toolchain.toml", ".rustfmt.toml"}
        for path in paths
    )
    python = validation or any(
        path.startswith(
            (
                "packages/boxie/",
                "tools/cli-playground/",
                "tools/cli-check/",
                "tools/cli-bench/",
                "infra/",
                ".github/",
            )
        )
        or path.endswith((".py", ".pyi", "/pyproject.toml", "/ruff.toml", "/.ruff.toml"))
        or path
        in {
            "pyproject.toml",
            "uv.lock",
            ".python-version",
            "ruff.toml",
            ".ruff.toml",
            ".dockerignore",
            "compose.yml",
        }
        for path in paths
    )
    frontend = validation or any(
        path.startswith(("tools/cli-playground/", "packages/boxie/"))
        or path in {"pyproject.toml", "uv.lock", ".python-version", "compose.yml", ".dockerignore"}
        for path in paths
    )
    return {
        "rust": rust,
        "python": python,
        "frontend": frontend,
        # Machete depends on imports as well as dependency manifests.
        "dependencies": rust or any(Path(path).name == "deny.toml" for path in paths),
        "codeql_rust": rust or ".github/workflows/codeql.yml" in paths,
        "codeql_actions": full or any(path.startswith(".github/") for path in paths),
    }


def changed_paths(root: Path, base: str, head: str) -> list[str]:
    result = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}", "--"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return result.stdout.decode("utf-8", errors="surrogateescape").split("\0")[:-1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--push-smoke", action="store_true")
    args = parser.parse_args()
    event_name = os.environ["GITHUB_EVENT_NAME"]
    smoke = event_name == "push" and args.push_smoke
    if event_name == "pull_request":
        event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
        pr = event["pull_request"]
        selected = select_jobs(
            changed_paths(ROOT, pr["base"]["sha"], pr["head"]["sha"]), cargo_directories(ROOT)
        )
    else:
        selected = dict.fromkeys(JOBS, not smoke)
    languages = [language for language in ("rust", "actions") if selected[f"codeql_{language}"]]
    outputs = {key: json.dumps(value) for key, value in selected.items()}
    outputs.update(smoke=json.dumps(smoke), languages=json.dumps(languages))
    with Path(os.environ["GITHUB_OUTPUT"]).open("a") as output:
        for key, value in outputs.items():
            print(f"{key}={value}", file=output)
    print(json.dumps(outputs, indent=2))


if __name__ == "__main__":
    main()
