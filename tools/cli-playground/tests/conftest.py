"""Reusable playground fixtures; no live Docker daemon is needed."""

import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from infra import deploy
from playground import app as playground_app
from playground.app import create_app
from playground.config import Settings

from .helpers import APP_ENV, GATEWAY_IMAGE, SANDBOX_IMAGE, git_commit, protected


@pytest.fixture
def api_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    static_dir = tmp_path / "frontend" / "dist"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text(
        '<html><script type="module" src="/static/assets/app-test.js"></script></html>'
    )
    (static_dir / "assets" / "app-test.js").write_text("document.title = 'Playground';")
    monkeypatch.setattr(playground_app, "STATIC_DIR", static_dir)
    settings = Settings(
        data_dir=tmp_path / "data",
        public_url="https://play.example",
        deploy_token="test-deployment-token",
        sandbox_image="clis-sandbox:test",
        max_binary_bytes=128,
    )
    with TestClient(create_app(settings), base_url=settings.public_url) as client:
        yield client


@pytest.fixture
def docker_settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        public_url="https://play.example",
        deploy_token="test-deployment-token",
        sandbox_image="clis-sandbox:test",
        max_sessions=1,
        session_seconds=5,
    )


@pytest.fixture(autouse=True)
def clean_settings_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in tuple(os.environ):
        if name.upper().startswith("PLAYGROUND_"):
            monkeypatch.delenv(name)


@pytest.fixture
def configured_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name, value in {
        "PLAYGROUND_DATA_DIR": str(tmp_path / "data"),
        "PLAYGROUND_PUBLIC_URL": "https://play.example/",
        "PLAYGROUND_DEPLOY_TOKEN": "private-deployment-token",
        "PLAYGROUND_SANDBOX_IMAGE": "sandbox:test",
    }.items():
        monkeypatch.setenv(name, value)


@pytest.fixture
def harness(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    # Do not let Git hook variables redirect fixture writes into the real checkout.
    for name in tuple(os.environ):
        if name.startswith("GIT_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "repo"
    root.mkdir()
    source = root / "tools/cli-playground"
    source.mkdir(parents=True)
    (source / "infra").mkdir()
    for name in (
        "compose.production.yml",
        "infra/routes.yml",
        "Gateway.Dockerfile",
        "Sandbox.Dockerfile",
    ):
        (source / name).write_text("committed source\n")
    assert deploy.__file__ is not None
    (source / "infra/deploy.sh").write_text(
        (Path(deploy.__file__).parent / "deploy.sh").read_text()
    )
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    git_commit(root)
    remote_root = tmp_path / "server/app"
    remote_config = tmp_path / "server/config"
    remote_root.mkdir(parents=True)
    remote_config.mkdir(mode=0o700)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    command_log = tmp_path / "commands.jsonl"
    shim = f"""#!{sys.executable}
import base64, json, os, pathlib, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ['DEPLOY_TEST_LOG'], 'a') as log:
    log.write(json.dumps([name, *args]) + '\\n')
if name == 'ssh':
    assert args[:6] == ['-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=15']
    boundary = args.index('--')
    if boundary > 6:
        assert args[6:9] == ['-F', '/dev/null', '-i']
        identity = pathlib.Path(args[9])
        known_hosts = pathlib.Path(args[13].removeprefix('UserKnownHostsFile='))
        assert identity.stat().st_mode & 0o777 == 0o600
        assert known_hosts.stat().st_mode & 0o777 == 0o600
        assert known_hosts.read_text() == '203.0.113.12 ssh-ed25519 YWJj\\n'
        assert args[10:13] == ['-o', 'IdentitiesOnly=yes', '-o']
        assert args[14:16] == ['-o', 'GlobalKnownHostsFile=/dev/null']
        assert args[boundary + 1] == 'root@203.0.113.12'
    else:
        assert args[boundary + 1] == 'root@preview.example'
    raise SystemExit(subprocess.run(['bash', '-c', args[boundary + 2]]).returncode)
if name == 'flock':
    assert args[:2] == ['-w', '120']
    raise SystemExit(subprocess.run(args[3:]).returncode)
if name == 'cloud-init':
    assert args == ['status', '--wait']
    if os.environ.get('DEPLOY_TEST_BOOTSTRAP_FAILED'):
        raise SystemExit(1)
if name == 'docker':
    if 'pull' in args and os.environ.get('DEPLOY_TEST_EXPECT_REGISTRY'):
        registry = pathlib.Path(os.environ['DOCKER_CONFIG']) / 'config.json'
        assert registry.stat().st_mode & 0o777 == 0o600
        auth = json.loads(registry.read_text())['auths']['ghcr.io']['auth']
        assert base64.b64decode(auth).decode() == os.environ['DEPLOY_TEST_EXPECT_REGISTRY']
    if args[0] == 'info':
        print('' if os.environ.get('DEPLOY_TEST_NO_RUNSC') else 'runsc')
    if 'pull' in args and os.environ.get('DEPLOY_TEST_FAIL_PULL') in (args[-1], 'any'):
        print('private-secret-from-remote-error', file=sys.stderr)
        raise SystemExit(1)
"""
    for name in ("ssh", "flock", "cloud-init", "docker"):
        script = bin_directory / name
        script.write_text(shim)
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_directory) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("DEPLOY_TEST_LOG", str(command_log))
    monkeypatch.setattr(deploy, "ROOT", root)
    monkeypatch.setattr(deploy, "REMOTE_ROOT", str(remote_root))
    monkeypatch.setattr(deploy, "REMOTE_CONFIG", str(remote_config))
    monkeypatch.setattr(deploy, "REMOTE_LOG", str(tmp_path / "remote-deploy.log"))

    def health(domain: str) -> None:
        with command_log.open("a") as log:
            log.write(json.dumps(["health", domain]) + "\n")

    monkeypatch.setattr(deploy, "wait_for_health", health)
    app = protected(tmp_path / "app.env", APP_ENV)
    revision = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True
    ).strip()
    release = tmp_path / "release.json"
    release.write_text(
        json.dumps(
            {"revision": revision, "gateway_image": GATEWAY_IMAGE, "sandbox_image": SANDBOX_IMAGE}
        )
    )
    return {
        "release": release,
        "root": root,
        "remote": remote_root,
        "config": remote_config,
        "app": app,
        "log": command_log,
        "remote_log": tmp_path / "remote-deploy.log",
    }
