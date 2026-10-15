"""Exercise deployment staging with real Git archives and a local remote-shell harness."""

import base64
import io
import json
import stat
import subprocess
import time
import urllib.request
from pathlib import Path

import pytest
from infra import deploy

from .helpers import APP_ENV, GATEWAY_IMAGE, SANDBOX_IMAGE, git_commit, protected


def test_explicit_ci_ssh_identity_pins_host_without_ambient_configuration(tmp_path: Path) -> None:
    command = deploy.ssh_command("192.0.2.1", tmp_path / "key", tmp_path / "known_hosts")
    assert command[-2:] == ["--", "root@192.0.2.1"]
    assert "StrictHostKeyChecking=yes" in command
    assert "IdentitiesOnly=yes" in command
    assert command[command.index("-F") + 1] == "/dev/null"
    assert command[command.index("-i") + 1] == str(tmp_path / "key")
    assert f"UserKnownHostsFile={tmp_path / 'known_hosts'}" in command


@pytest.mark.parametrize("fail", [False, True])
def test_private_registry_credentials_exist_only_during_remote_deployment(
    harness: dict[str, Path], monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    monkeypatch.setenv("DEPLOY_TEST_EXPECT_REGISTRY", "actor:ephemeral-token")
    if fail:
        monkeypatch.setenv("DEPLOY_TEST_FAIL_PULL", "any")
    try:
        deploy.deploy(
            "preview.example",
            harness["app"],
            harness["release"],
            registry_credentials=("actor", "ephemeral-token"),
        )
    except RuntimeError:
        assert fail
    else:
        assert not fail
    for path in harness["remote"].rglob("*"):
        if path.is_file():
            assert b"ephemeral-token" not in path.read_bytes()
            assert base64.b64encode(b"actor:ephemeral-token") not in path.read_bytes()
    assert not list(harness["remote"].rglob("config.json"))
    assert "ephemeral-token" not in harness["log"].read_text()


def test_deploy_refuses_unsafe_hosts_before_reading_secrets() -> None:
    with pytest.raises(ValueError, match="host"):
        deploy.deploy("example.org;true", Path("missing-app"), Path("release.json"))


def test_deploy_requires_private_file_permissions(tmp_path: Path) -> None:
    app = protected(tmp_path / "app.env", APP_ENV)
    app.chmod(0o644)
    with pytest.raises(ValueError, match="0600"):
        deploy.deploy("192.0.2.1", app, Path("release.json"))


def test_deploy_refuses_symlinked_secret_files(tmp_path: Path) -> None:
    source = protected(tmp_path / "source.env", APP_ENV)
    app = tmp_path / "app.env"
    app.symlink_to(source)
    with pytest.raises((ValueError, OSError)):
        deploy.deploy("preview.example", app, Path("release.json"))


@pytest.mark.parametrize(
    "data",
    [
        APP_ENV.replace(b"local-test-secret", b"${TOKEN}"),
        APP_ENV.replace(b"private-renewal-token", b"${TOKEN}"),
        APP_ENV.replace(b"CLOUDFLARE_DNS_API_TOKEN=private-renewal-token\n", b""),
        APP_ENV + b"UNKNOWN_FIELD=extra-value\n",
    ],
)
def test_invalid_app_configuration_is_rejected_before_network(tmp_path: Path, data: bytes) -> None:
    app = protected(tmp_path / "app.env", data)
    with pytest.raises(ValueError):
        deploy.deploy("preview.example", app, Path("release.json"))


def commands(harness: dict[str, Path]) -> list[list[str]]:
    return [json.loads(line) for line in harness["log"].read_text().splitlines()]


def test_deploy_uploads_only_committed_configuration_and_pulls_before_starting(
    harness: dict[str, Path],
) -> None:
    (harness["root"] / "tools/cli-playground/compose.production.yml").write_text(
        "uncommitted edit\n"
    )
    protected(harness["root"] / ".env", b"untracked-private-secret")
    (harness["root"] / "tools/cli-playground/infra/deploy.sh").write_text("exit 99\n")
    revision = json.loads(harness["release"].read_text())["revision"]
    assert deploy.deploy("preview.example", harness["app"], harness["release"]) == (
        revision,
        "https://play.example/healthz",
    )
    releases = list((harness["remote"] / "releases").iterdir())
    assert len(releases) == 1
    release = releases[0]
    uploaded = {
        path.relative_to(release).as_posix() for path in release.rglob("*") if path.is_file()
    }
    assert uploaded == {
        "tools/cli-playground/compose.production.yml",
        "tools/cli-playground/infra/routes.yml",
        "tools/cli-playground/infra/deploy.sh",
        "release.json",
        "image.env",
    }
    assert (
        release / "tools/cli-playground/compose.production.yml"
    ).read_text() == "committed source\n"
    assert json.loads((release / "release.json").read_text()) == json.loads(
        harness["release"].read_text()
    )
    assert (harness["config"] / "app.env").read_bytes() == APP_ENV
    assert {path.name for path in harness["config"].iterdir()} == {"app.env"}
    assert stat.S_IMODE((harness["config"] / "app.env").stat().st_mode) == 0o600
    calls = commands(harness)
    assert calls[-2:] == [["health", "play.example"], ["health", "pr-1.play.example"]]
    docker = [call[1:] for call in calls if call[0] == "docker"]
    assert [call for call in docker if call[0] == "pull"] == [
        ["pull", "--platform", "linux/amd64", GATEWAY_IMAGE],
        ["pull", "--platform", "linux/amd64", SANDBOX_IMAGE],
    ]
    assert not any("build" in call for call in docker)
    assert docker[-2][-2:] == ["pull", "proxy"]
    assert "up" in docker[-1] and "--no-build" in docker[-1]
    assert docker[-1][docker[-1].index("--pull") + 1] == "never"
    assert "local-test-secret" not in harness["log"].read_text()
    assert "private-renewal-token" not in harness["log"].read_text()
    assert all(
        b"private-renewal-token" not in path.read_bytes()
        for path in release.rglob("*")
        if path.is_file()
    )
    assert not list((harness["remote"] / "releases").glob(".deploy.*"))


def test_pull_failure_preserves_existing_secrets_and_services_and_hides_remote_output(
    harness: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    protected(harness["config"] / "app.env", b"old application config")
    monkeypatch.setenv("DEPLOY_TEST_FAIL_PULL", "proxy")
    with pytest.raises(RuntimeError) as error:
        deploy.deploy("preview.example", harness["app"], harness["release"])
    output = capsys.readouterr()
    assert "private-secret-from-remote-error" not in str(error.value) + output.out + output.err
    assert "private-secret-from-remote-error" in harness["remote_log"].read_text()
    assert stat.S_IMODE(harness["remote_log"].stat().st_mode) == 0o600
    assert (harness["config"] / "app.env").read_bytes() == b"old application config"
    assert not any("up" in call for call in commands(harness))
    assert not list((harness["remote"] / "releases").iterdir())


@pytest.mark.parametrize(
    "field,value",
    [
        ("revision", "a" * 40),
        ("gateway_image", "ghcr.io/team/gateway:latest"),
    ],
)
def test_mismatched_revision_or_invalid_image_is_refused_before_ssh(
    harness: dict[str, Path], field: str, value: object
) -> None:
    manifest = json.loads(harness["release"].read_text())
    manifest[field] = value
    harness["release"].write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="release|revision|image"):
        deploy.deploy("preview.example", harness["app"], harness["release"])
    assert not harness["log"].exists()


def test_oversized_manifest_is_refused_before_ssh(harness: dict[str, Path]) -> None:
    harness["release"].write_bytes(b" " * 65536)
    with pytest.raises(ValueError, match="release"):
        deploy.deploy("preview.example", harness["app"], harness["release"])
    assert not harness["log"].exists()


def test_rebuilding_one_commit_keeps_distinct_release_metadata(harness: dict[str, Path]) -> None:
    deploy.deploy("preview.example", harness["app"], harness["release"])
    second = json.loads(harness["release"].read_text())
    second["gateway_image"] = "ghcr.io/team/gateway@sha256:" + "3" * 64
    harness["release"].write_text(json.dumps(second))
    deploy.deploy("preview.example", harness["app"], harness["release"])
    releases = list((harness["remote"] / "releases").iterdir())
    assert len(releases) == 2
    assert {
        json.loads((release / "release.json").read_text())["gateway_image"] for release in releases
    } == {GATEWAY_IMAGE, second["gateway_image"]}


def test_missing_runsc_prevents_pulls_and_service_changes(
    harness: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEPLOY_TEST_NO_RUNSC", "1")
    with pytest.raises(RuntimeError):
        deploy.deploy("preview.example", harness["app"], harness["release"])
    assert not any("pull" in call or "up" in call for call in commands(harness))


def test_failed_bootstrap_prevents_secret_install_and_pulls(
    harness: dict[str, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DEPLOY_TEST_BOOTSTRAP_FAILED", "1")
    with pytest.raises(RuntimeError):
        deploy.deploy("preview.example", harness["app"], harness["release"])
    assert not list(harness["config"].iterdir())
    assert not any(call[0] in ("flock", "docker") for call in commands(harness))


def test_tracked_secret_file_is_refused_before_archive_or_ssh(harness: dict[str, Path]) -> None:
    tracked = protected(harness["root"] / "private.env", APP_ENV)
    subprocess.run(["git", "-C", str(harness["root"]), "add", "private.env"], check=True)
    git_commit(harness["root"])
    with pytest.raises(ValueError, match="tracked"):
        deploy.deploy("preview.example", tracked, harness["release"])
    assert not harness["log"].exists()


@pytest.mark.parametrize(
    "responses,succeeds",
    [
        ([b'{"status":"starting"}', b'{"status":"ok"}'], True),
        ([b'{"status":"starting"}'] * 2, False),
    ],
)
def test_public_health_retries_until_ready_or_timeout(
    monkeypatch: pytest.MonkeyPatch, responses: list[bytes], succeeds: bool
) -> None:
    elapsed = [0.0]

    class Opener:
        def open(self, url: str, *, timeout: float) -> io.BytesIO:
            assert url == "https://play.example/healthz" and 0 < timeout <= 5
            return io.BytesIO(responses.pop(0))

    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: Opener())
    monkeypatch.setattr(deploy, "HEALTH_TIMEOUT", 10)
    monkeypatch.setattr(time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    if succeeds:
        deploy.wait_for_health("play.example")
    else:
        with pytest.raises(RuntimeError, match="Services started.*HTTPS health"):
            deploy.wait_for_health("play.example")
