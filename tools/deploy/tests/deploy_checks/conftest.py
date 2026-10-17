"""Shared isolated component source fixtures."""

import json
import os
from pathlib import Path

import pytest

DIGEST = "registry.example/test@sha256:" + "a" * 64


@pytest.fixture
def source(tmp_path: Path) -> Path:
    (tmp_path / "compose.yml").write_text(
        "services:\n  web:\n    image: ${APP_IMAGE}\n"
        "    networks: [private]\nnetworks:\n  private:\n    external: true\n"
    )
    (tmp_path / "descriptor.json").write_text(
        json.dumps(
            {
                "component": "sample",
                "compose_file": "compose.yml",
                "files": [],
                "services": ["web"],
                "image_variables": {"APP_IMAGE": ["web"]},
                "required_environment": [],
                "secret_environment": ["TOKEN"],
                "compatibility": {"schema": "sample-v1", "dependency": "persistence-v1"},
                "healthchecks": {"web": {"kind": "docker"}},
            }
        )
    )
    (tmp_path / "release.json").write_text(
        json.dumps(
            {
                "component": "sample",
                "revision": "1" * 40,
                "images": {"APP_IMAGE": DIGEST},
                "compatibility": {"schema": "sample-v1", "dependency": "persistence-v1"},
            }
        )
    )
    (tmp_path / "env.json").write_text(json.dumps({"TOKEN": "never-log-me"}))
    return tmp_path


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
