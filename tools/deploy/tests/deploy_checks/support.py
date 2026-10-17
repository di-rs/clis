"""Committed-source fixture support; never inherits caller Git state."""

import json
import os
from pathlib import Path


def commit_source(source: Path) -> str:
    import subprocess

    local = subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).splitlines()
    env = {k: v for k, v in os.environ.items() if k not in local}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(source), *args], text=True, env=env
        ).strip()

    if not (source / ".git").exists():
        git("init", "-q")
    descriptor = json.loads((source / "descriptor.json").read_text())
    selected = {
        "descriptor.json",
        descriptor.get("compose_file", "compose.yml"),
        *descriptor.get("files", []),
    }
    if descriptor.get("pre_activate"):
        selected.add(descriptor["pre_activate"])
    selected = {
        next(
            (
                str(parent)
                for parent in reversed(Path(f).parents)
                if str(parent) != "." and (source / parent).is_symlink()
            ),
            f,
        )
        for f in selected
    }
    git("add", "--", *(f for f in selected if (source / f).exists() and ".." not in Path(f).parts))
    if git("diff", "--cached", "--name-only") == "":
        return git("rev-parse", "HEAD")
    git(
        "-c",
        "user.name=Release test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "fixture",
        "--allow-empty",
    )
    return git("rev-parse", "HEAD")
