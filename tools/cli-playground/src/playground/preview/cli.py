"""Installed preview CLI; source operations require --repository."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ci.github import GitHub

from .build import discover, select
from .lifecycle import lifecycle
from .publication import publish


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--repository", required=True, type=Path)
    build.add_argument("--base", required=True)
    build.add_argument("--head", required=True)
    build.add_argument("--output", required=True, type=Path)
    publication = commands.add_parser("publish")
    publication.add_argument("--repo", required=True)
    publication.add_argument("--run-id", required=True, type=int)
    lifecycle_command = commands.add_parser("lifecycle")
    lifecycle_command.add_argument("--repo", required=True)
    lifecycle_command.add_argument("--pr", required=True, type=int)
    lifecycle_command.add_argument("--action", required=True, choices=("closed", "reopened"))
    args = parser.parse_args()
    try:
        if args.command == "build":
            root = args.repository.resolve()
            diff = subprocess.run(
                [
                    "git",
                    "diff",
                    "--name-only",
                    "--no-renames",
                    "-z",
                    f"{args.base}...{args.head}",
                    "--",
                ],
                cwd=root,
                check=True,
                capture_output=True,
            )
            paths = [os.fsdecode(item) for item in diff.stdout.split(b"\0") if item]
            names = select(discover(root), paths)
            selection = "CLI playground builds: " + (", ".join(names) or "none")
            print(selection)
            if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
                with Path(summary).open("a") as stream:
                    print(selection, file=stream)
            if names:
                subprocess.run(
                    [
                        "cargo",
                        "build",
                        "--locked",
                        "--release",
                        "--bins",
                        *(arg for name in names for arg in ("--package", name)),
                    ],
                    cwd=root,
                    check=True,
                )
                args.output.mkdir(parents=True, exist_ok=False)
                for name in names:
                    shutil.copy2(root / "target/release" / name, args.output / name)
        else:
            api = GitHub()
            base_url = os.environ.get("PLAYGROUND_URL", "")
            token = os.environ.get("PLAYGROUND_DEPLOY_TOKEN", "")
            if args.command == "lifecycle":
                message = lifecycle(api, args.repo, args.pr, args.action, base_url, token)
            else:
                message = publish(api, args.repo, args.run_id, base_url, token)
            if message:
                print(message)
    except OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError:
        sys.exit("Playground CI failed; check configuration, build logs, and artifact validity.")


if __name__ == "__main__":
    main()
