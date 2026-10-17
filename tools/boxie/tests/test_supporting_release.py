"""Native supporting releases select one owner and exclude schema/init operations."""

import json
import os
import subprocess
from pathlib import Path

import pytest


def test_native_owner_release_is_committed_targeted_and_private(tmp_path: Path) -> None:
    from boxie_infra.operations import activate_supporting

    owner = tmp_path / "tools/boxie/infra/postgres"
    owner.mkdir(parents=True)
    (owner / "compose.yml").write_text(
        "services:\n  postgres:\n    image: docker.io/library/postgres@sha256:" + "a" * 64 + "\n"
    )
    (owner / "compose.production.yml").write_text("services: {}\n")
    local = subprocess.check_output(
        ["git", "rev-parse", "--local-env-vars"], text=True
    ).splitlines()
    env = {k: v for k, v in os.environ.items() if k not in local}

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(tmp_path), *args], text=True, env=env
        ).strip()

    git("init", "-q")
    git("add", ".")
    git(
        "-c",
        "user.name=Owner test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "core.hooksPath=/dev/null",
        "commit",
        "-qm",
        "fixture",
    )
    revision = git("rev-parse", "HEAD")
    calls: list[list[str]] = []

    def run(argv: list[str], env: dict[str, str], cwd: Path, seconds: float) -> str:
        calls.append(argv)
        assert "never-print" not in " ".join(argv)
        if "config" in argv:
            return json.dumps(
                {
                    "services": {
                        "postgres": {"image": "docker.io/library/postgres@sha256:" + "a" * 64}
                    }
                }
            )
        return ""

    result = activate_supporting(
        tmp_path, revision, "postgres", {"POSTGRES_PASSWORD": "never-print"}, run=run
    )
    assert result["unit"] == "postgres" and result["revision"] == revision
    assert result["services"] == ["postgres"]
    assert "never-print" not in json.dumps(result)
    assert calls[-1][-1] == "postgres" and "--no-deps" in calls[-1]
    assert not any(
        arg in {"schema", "bootstrap", "storage-init", "down", "--volumes"}
        for call in calls
        for arg in call
    )
    (owner / "compose.yml").write_text("uncommitted")
    calls.clear()
    with pytest.raises(ValueError, match="committed"):
        activate_supporting(tmp_path, revision, "postgres", {}, run=run)
    assert calls == []


def test_owner_workflows_are_manual_and_credential_scoped() -> None:
    import yaml

    root = Path(__file__).parents[3] / ".github/workflows"
    for name in ("boxie-supporting-release.yml", "boxie-profiles-release.yml"):
        config = yaml.load((root / name).read_text(), Loader=yaml.BaseLoader)
        assert set(config["on"]) == {"workflow_dispatch"}
        assert "environment" not in config["jobs"]["revision"]
        job = config["jobs"]["activate"]
        assert job["needs"] == "revision"
        assert job["concurrency"]["cancel-in-progress"] == "false"
        encoded = json.dumps(job)
        assert not any(
            name in encoded
            for name in ("HCLOUD_TOKEN", "CLOUDFLARE_API_TOKEN", "PULUMI_CONFIG_PASSPHRASE")
        )
