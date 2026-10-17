"""Native supporting releases select one owner and exclude schema/init operations."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.integration
@pytest.mark.parametrize(
    "unit, project",
    [
        ("postgres", "boxie-postgres"),
        ("temporal", "boxie-temporal"),
        ("observability", "boxie-observability"),
        ("ingress", "boxie-operator-ingress"),
    ],
)
def test_supporting_release_targets_installed_compose_project(
    tmp_path: Path, unit: str, project: str
) -> None:
    from boxie_infra.operations import activate_supporting

    owner = tmp_path / "tools/boxie/infra" / unit
    shutil.copytree(Path(__file__).parents[1] / "infra" / unit, owner)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in (
        ["init", "-q"],
        ["add", "."],
        [
            "-c",
            "user.name=Owner test",
            "-c",
            "user.email=test@example.com",
            "-c",
            "core.hooksPath=/dev/null",
            "commit",
            "-qm",
            "fixture",
        ],
    ):
        subprocess.run(["git", "-C", str(tmp_path), *args], env=env, check=True)
    revision = subprocess.check_output(
        ["git", "-C", str(tmp_path), "rev-parse", "HEAD"], env=env, text=True
    ).strip()
    values = {
        key: str(tmp_path / key.lower())
        for path in owner.glob("compose*.yml")
        for key in re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", path.read_text())
    }
    values.update(
        BOXIE_CONTROL_NETWORK="owner-test-control",
        POSTGRES_VOLUME="owner-test-postgres",
        OBS_COLLECTOR_IMAGE="registry.example/collector@sha256:" + "a" * 64,
        OBS_JOURNAL_GROUP_GID="0",
        TEMPORAL_PROBE_ADDRESS="127.0.0.1:7233",
    )
    installed = ["docker", "compose", "--env-file", "/dev/null", "-f", str(owner / "compose.yml")]
    if (owner / "compose.production.yml").exists():
        installed += ["-f", str(owner / "compose.production.yml")]
    initial = json.loads(
        subprocess.check_output(
            installed + ["config", "--format", "json"], env={**os.environ, **values}, text=True
        )
    )
    assert initial["name"] == project
    resolved: list[str] = []

    class BeforeMutation(Exception):
        pass

    def run(argv: list[str], env: dict[str, str], cwd: Path, seconds: float) -> str:
        if "config" in argv:
            output = subprocess.check_output(argv, env=env, cwd=cwd, text=True)
            resolved.append(json.loads(output)["name"])
            return output
        raise BeforeMutation

    with pytest.raises(BeforeMutation):
        activate_supporting(tmp_path, revision, unit, dict(values), run=run)
    assert resolved == [project], "release must update the project's installed services"


@pytest.mark.integration
def test_assembled_supporting_release_preserves_installed_postgres(tmp_path: Path) -> None:
    from integration.deployment import DeploymentStack

    stack = DeploymentStack(tmp_path, monitoring=False)
    # Fail before acquiring Docker resources if the assembled release adapter is absent.
    release = stack.supporting_release
    stack.env["POSTGRES_VOLUME"] = stack.name + "-data"
    stack.write_overrides()
    stack.command(["docker", "network", "create", stack.name])
    stack.command(["docker", "volume", "create", stack.env["POSTGRES_VOLUME"]])
    try:
        stack.compose("postgres", "up", "-d", "--wait", "--wait-timeout", "90")
        stack.compose(
            "postgres",
            "exec",
            "-T",
            "postgres",
            "sh",
            "-ec",
            "printf retained > /var/lib/postgresql/data/release-sentinel",
        )
        before = stack.compose("postgres", "ps", "-q", "postgres")
        source = stack.supporting_sources["postgres"]
        committed = stack.git(source, "show", "HEAD:tools/boxie/infra/postgres/compose.yml")
        assert stack.env["POSTGRES_PASSWORD"] not in committed
        result = release("postgres")
        assert result["outcome"] == "activated"
        assert stack.compose("postgres", "ps", "-q", "postgres") == before
        assert (
            stack.compose(
                "postgres",
                "exec",
                "-T",
                "postgres",
                "cat",
                "/var/lib/postgresql/data/release-sentinel",
            )
            == "retained"
        )
    finally:
        stack.compose("postgres", "down", "--remove-orphans")
        stack.command(["docker", "volume", "rm", stack.env["POSTGRES_VOLUME"]])
        stack.command(["docker", "network", "rm", stack.name])


@pytest.mark.integration
@pytest.mark.parametrize("unit", ["temporal", "observability"])
def test_assembled_supporting_checkout_preserves_dynamic_bind_mounts(
    tmp_path: Path, unit: str
) -> None:
    from integration.deployment import DeploymentStack

    stack = DeploymentStack(tmp_path, monitoring=True)
    owner = Path(__file__).parents[1] / "infra" / unit
    for path in owner.glob("compose*.yml"):
        for key in re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", path.read_text()):
            stack.env.setdefault(key, str(tmp_path / key.lower()))
    stack.env.update(
        OBS_COLLECTOR_IMAGE="registry.example/collector@sha256:" + "a" * 64,
        OBS_JOURNAL_GROUP_GID="0",
    )
    stack.write_overrides()
    config = json.loads(stack.compose(unit, "config", "--format", "json"))
    services = config["services"]
    targets = (
        {"temporal": "/certs", "ui": "/certs"}
        if unit == "temporal"
        else {"prometheus": "/etc/prometheus/prometheus.yml", "grafana": "/var/lib/grafana/plugins"}
    )
    for service, target in targets.items():
        mount = next(
            volume for volume in services[service]["volumes"] if volume["target"] == target
        )
        assert mount["type"] == "bind"
        assert Path(mount["source"]).is_absolute()


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
