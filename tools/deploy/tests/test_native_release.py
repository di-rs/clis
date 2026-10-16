"""One bounded real-Compose release/rollback gate, isolated by random owned resources."""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest
from deploy_checks.test_cli import PACKAGE, invoke
from deploy_tool.prepare import load_prepared

OWNER = "com.di-rs.clis.release-test"
SERVER = """import http.server, os
from pathlib import Path
sentinel = Path('/state/sentinel')
if not sentinel.exists():
    sentinel.write_text('retained-state')
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if os.environ['HEALTHY'] == 'yes' else 503)
        self.end_headers()
    def log_message(self, *args):
        pass
http.server.HTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
"""


def docker(*args: str, timeout: float = 30) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, timeout=timeout
    )
    assert result.returncode == 0, f"Docker prerequisite/operation failed: {args[:2]}"
    return result.stdout.strip()


def activate(prepared: Path, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PACKAGE / "deploy_tool/remote.py"),
            "--root",
            str(root),
            "--timeout",
            "120",
            "--health-timeout",
            "20",
        ],
        input=json.dumps(load_prepared(prepared)),
        capture_output=True,
        text=True,
        check=False,
        timeout=130,
    )


def component(
    directory: Path,
    name: str,
    image: str,
    network: str,
    volume: str,
    owner: str,
    healthy: bool,
) -> Path:
    directory.mkdir()
    (directory / "server.py").write_text(SERVER)
    (directory / "compose.yml").write_text(f"""services:
  web:
    image: ${{APP_IMAGE:?Set pinned image}}
    command: [python, /app/server.py]
    environment:
      HEALTHY: ${{HEALTHY}}
    labels:
      {OWNER}: {owner}
    healthcheck:
      test: [CMD, python, -c, "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/', timeout=1)"]
      interval: 1s
      timeout: 2s
      retries: 2
    volumes:
    - ${{CLIS_RELEASE_DIR}}/server.py:/app/server.py:ro
    - data:/state
    networks: [private]
volumes:
  data:
    external: true
    name: {volume}
networks:
  private:
    external: true
    name: {network}
""")
    descriptor: dict[str, Any] = {
        "component": name,
        "compose_file": "compose.yml",
        "files": ["server.py"],
        "services": ["web"],
        "image_variables": {"APP_IMAGE": ["web"]},
        "required_environment": ["HEALTHY"],
        "healthchecks": {"web": {"kind": "docker"}},
    }
    (directory / "descriptor.json").write_text(json.dumps(descriptor))
    (directory / "release.json").write_text(
        json.dumps(
            {
                "component": name,
                "revision": ("a" if healthy else "b") * 40,
                "images": {"APP_IMAGE": image},
                "compatibility": {"schema": "fixture-v1"},
            }
        )
    )
    (directory / "environment.json").write_text(json.dumps({"HEALTHY": "yes" if healthy else "no"}))
    prepared = directory / "prepared"
    result = invoke(
        "prepare",
        "--descriptor",
        str(directory / "descriptor.json"),
        "--release",
        str(directory / "release.json"),
        "--environment",
        str(directory / "environment.json"),
        "--output",
        str(prepared),
    )
    assert result.returncode == 0, result.stderr
    return prepared


def owned_cleanup(owner: str, projects: set[str], networks: list[str], volumes: list[str]) -> None:
    errors: list[Exception] = []
    containers = docker("ps", "-aq", "--filter", f"label={OWNER}={owner}").split()
    for container in containers:
        try:
            metadata = json.loads(docker("inspect", container))[0]
            labels = metadata["Config"]["Labels"]
            assert labels.get(OWNER) == owner
            assert labels.get("com.docker.compose.project") in projects
            docker("rm", "-f", container)
        except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
            errors.append(error)
    for kind, resources in (("network", networks), ("volume", volumes)):
        for resource in resources:
            try:
                metadata = json.loads(docker(kind, "inspect", resource))[0]
                assert metadata["Labels"].get(OWNER) == owner
                docker(kind, "rm", resource)
            except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
                errors.append(error)
    if errors:
        raise ExceptionGroup("owned release fixture cleanup failed", errors)


@pytest.mark.integration
def test_real_compose_independent_update_and_failed_release_rollback(tmp_path: Path) -> None:
    assert docker("info", "--format", "{{.OSType}}") == "linux"
    docker("pull", "python:3.15-slim", timeout=180)
    digests = json.loads(
        docker("image", "inspect", "--format", "{{json .RepoDigests}}", "python:3.15-slim")
    )
    assert digests and "@sha256:" in digests[0]
    image = str(digests[0])
    owner = uuid.uuid4().hex[:12]
    network = "clis-release-" + owner
    volumes = [network + "-a", network + "-b"]
    names = ["native-a-" + owner, "native-b-" + owner]
    projects = {"clis-" + name for name in names}
    created_networks: list[str] = []
    created_volumes: list[str] = []
    root = tmp_path / "host"
    try:
        docker("network", "create", "--label", f"{OWNER}={owner}", network)
        created_networks.append(network)
        for volume in volumes:
            docker("volume", "create", "--label", f"{OWNER}={owner}", volume)
            created_volumes.append(volume)
        first = component(tmp_path / "first", names[0], image, network, volumes[0], owner, True)
        other = component(tmp_path / "other", names[1], image, network, volumes[1], owner, True)
        for prepared in (first, other):
            result = activate(prepared, root)
            assert result.returncode == 0, result.stdout + result.stderr
        previous = (root / names[0] / "current").resolve()
        first_id = docker(
            "ps", "-q", "--filter", f"label=com.docker.compose.project=clis-{names[0]}"
        )
        sentinel = "retained-" + uuid.uuid4().hex
        docker(
            "exec",
            first_id,
            "python",
            "-c",
            "from pathlib import Path; import sys; Path('/state/sentinel').write_text(sys.argv[1])",
            sentinel,
        )
        other_id = docker(
            "ps", "-q", "--filter", f"label=com.docker.compose.project=clis-{names[1]}"
        )
        assert other_id
        before = json.loads(docker("inspect", other_id))[0]["State"]["StartedAt"]
        failed = component(tmp_path / "failed", names[0], image, network, volumes[0], owner, False)
        result = activate(failed, root)
        assert result.returncode == 1, result.stdout + result.stderr
        assert json.loads(result.stdout)["outcome"] == "failed-rolled-back"
        assert (root / names[0] / "current").resolve() == previous
        current_id = docker(
            "ps", "-q", "--filter", f"label=com.docker.compose.project=clis-{names[0]}"
        )
        assert (
            docker(
                "exec",
                current_id,
                "python",
                "-c",
                "from pathlib import Path; print(Path('/state/sentinel').read_text())",
            )
            == sentinel
        )
        assert (
            docker("ps", "-q", "--filter", f"label=com.docker.compose.project=clis-{names[1]}")
            == other_id
        )
        assert json.loads(docker("inspect", other_id))[0]["State"]["StartedAt"] == before
    finally:
        owned_cleanup(owner, projects, created_networks, created_volumes)
