"""Independent service packaging and operator-owned topology contracts."""

import json
import tomllib
from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    "relative,module",
    [
        ("tools/boxie", "boxie"),
        ("tools/cli-playground", "playground"),
        ("tools/boxie/infra", "boxie_infra"),
        ("tools/cli-playground/infra", "playground_infra"),
    ],
)
def test_distributions_have_explicit_isolated_modules(relative: str, module: str) -> None:
    config = tomllib.loads((ROOT / relative / "pyproject.toml").read_text())
    if module == "boxie":
        assert config["build-system"]["build-backend"] == "hatchling.build"
        assert config["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["src/boxie"]
    else:
        assert config["build-system"] == {
            "requires": ["uv_build==0.13.0"],
            "build-backend": "uv_build",
        }
        assert config["tool"]["uv"]["build-backend"]["module-name"] == module


@pytest.mark.parametrize(
    "dockerfile,package",
    [
        ("tools/boxie/infra/Runtime.Dockerfile", "boxie"),
        ("tools/cli-playground/infra/Playground.Dockerfile", "cli-playground"),
    ],
)
def test_service_image_staged_workspace_resolves(
    tmp_path: Path, dockerfile: str, package: str
) -> None:
    import os
    import shlex
    import shutil
    import subprocess

    # Reproduce only metadata actually staged before sync, including manifests
    # inside directory COPYs. No full checkout can hide a missing workspace member.
    for line in (ROOT / dockerfile).read_text().splitlines():
        if line.startswith("RUN uv sync "):
            break
        if not line.startswith("COPY "):
            continue
        tokens = shlex.split(line)[1:]
        if tokens[0].startswith("--"):
            continue
        for source in tokens[:-1]:
            path = ROOT / source
            if path.is_dir():
                path = path / "pyproject.toml"
            if path.name not in {"pyproject.toml", "uv.lock", ".python-version"}:
                continue
            if not path.is_file():
                continue
            target = tmp_path / path.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    result = subprocess.run(
        ["uv", "export", "--locked", "--offline", "--package", package, "--no-dev", "--no-hashes"],
        cwd=tmp_path,
        env={key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "fastapi" in result.stdout


def test_private_compose_units_do_not_share_privileges() -> None:
    boxie = yaml.safe_load((ROOT / "tools/boxie/infra/compose.yml").read_text())
    product = yaml.safe_load((ROOT / "tools/cli-playground/infra/compose.yml").read_text())
    assert set(boxie["services"]) == {"api", "orchestrator"}
    for name, service in boxie["services"].items():
        assert not service.get("ports")
        assert ("docker.sock" in json.dumps(service)) == (name == "orchestrator")
    assert "docker.sock" not in json.dumps(product)
    assert not product["services"]["gateway"].get("depends_on")
    assert "PLAYGROUND_BOXIE_URL" in product["services"]["gateway"]["environment"]
    assert boxie["networks"]["consumer"]["external"] is True
    assert product["networks"]["consumer"]["external"] is True


@pytest.mark.parametrize(
    "component,directory,services",
    [
        ("boxie", "tools/boxie/infra", {"api", "orchestrator"}),
        ("playground", "tools/cli-playground/infra", {"gateway", "proxy"}),
    ],
)
def test_production_descriptors_validate_independent_release_ownership(
    component: str, directory: str, services: set[str]
) -> None:
    from deploy_tool.manifests import validate

    root = ROOT / directory
    descriptor = json.loads((root / "release-component.json").read_text())
    files = {
        name: (root / name).read_bytes()
        for name in {descriptor["compose_file"], *descriptor["files"]}
    }
    manifest = validate(descriptor, json.loads((root / "release.example.json").read_text()), files)
    assert manifest.component == component
    assert set(cast(list[str], manifest.descriptor["services"])) == services
    config = yaml.safe_load(manifest.files["compose.production.yml"])
    for name, service in config["services"].items():
        assert ("docker.sock" in json.dumps(service)) == (name == "orchestrator")
        if name != "proxy":
            assert service["user"] == "10001:10001"
            assert service["mem_limit"]
            assert service["pids_limit"] == 256


@pytest.mark.integration
@pytest.mark.parametrize(
    "package,module,forbidden",
    [("boxie", "boxie", "playground"), ("cli-playground", "playground", "boxie")],
)
def test_real_isolated_installation(
    tmp_path: Path, package: str, module: str, forbidden: str
) -> None:
    import os
    import subprocess
    import sys

    service = ROOT / "tools" / package
    if package == "cli-playground":
        assert (service / "src/playground/static/index.html").is_file(), (
            "Build the real frontend first"
        )
    wheels = tmp_path / "wheels"
    environment = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "VIRTUAL_ENV"}
    }
    subprocess.run(
        ["uv", "build", "--package", package, "--out-dir", str(wheels)],
        cwd=ROOT,
        env=environment,
        check=True,
    )
    python = tmp_path / "venv/bin/python"
    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(python.parent.parent)],
        env=environment,
        check=True,
    )
    (wheel,) = wheels.glob("*.whl")
    constraints = tmp_path / "constraints.txt"
    subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--package",
            package,
            "--no-dev",
            "--no-emit-workspace",
            "--no-hashes",
            "--output-file",
            str(constraints),
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(python),
            "--constraint",
            str(constraints),
            str(wheel),
        ],
        env=environment,
        check=True,
    )
    probe = f"""
import importlib, importlib.util, pathlib
module = importlib.import_module({module!r})
assert pathlib.Path(module.__file__).is_relative_to({str(python.parent.parent)!r})
for name in ({forbidden!r}, 'boxie_infra', 'playground_infra', 'boxie_test_support', 'pulumi', 'pytest'):
    assert importlib.util.find_spec(name) is None, name
"""
    if module == "boxie":
        probe += (
            "import boxie.bootstrap.api, boxie.bootstrap.orchestrator, boxie.bootstrap.runtime\n"
            "from io import StringIO\n"
            "from alembic import command\n"
            "from boxie.persistence.migrate import migration_config\n"
            "output = StringIO()\n"
            "config = migration_config('postgresql://localhost/boxie')\n"
            "config.output_buffer = output\n"
            "assert pathlib.Path(config.get_main_option('script_location'), 'script.py.mako').is_file()\n"
            "command.upgrade(config, 'head', sql=True)\n"
            "assert 'CREATE TABLE sandboxes' in output.getvalue()\n"
        )
    else:
        # Real installed assets via ASGI HTTP, without pytest/httpx or source imports.
        probe += """
import asyncio, json, re
from playground.app import create_app, STATIC_DIR
from playground.config import Settings
secret = pathlib.Path('consumer.secret'); secret.write_text('isolated-consumer-secret'); secret.chmod(0o600)
app = create_app(Settings(data_dir=pathlib.Path.cwd()/'data', public_url='http://localhost', deploy_token='local-token', boxie_url='http://127.0.0.1:1', boxie_credential_file=secret.absolute()))
async def request(path):
    sent = []
    received = False
    async def receive():
        nonlocal received
        if not received:
            received = True
            return {'type':'http.request', 'body':b'', 'more_body':False}
        await asyncio.Event().wait()
        raise AssertionError('unreachable')
    async def send(message): sent.append(message)
    await app({'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':'GET','scheme':'http','path':path,'raw_path':path.encode(),'query_string':b'','headers':[(b'host',b'localhost')],'server':('localhost',80),'client':('127.0.0.1',123)}, receive, send)
    assert sent[0]['status'] == 200, sent
    return b''.join(m.get('body',b'') for m in sent)
async def run():
    html = await request('/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa')
    assert html == (STATIC_DIR/'index.html').read_bytes()
    paths = re.findall(rb'(?:src|href)="(/static/[^\"]+)"', html)
    assert paths
    for path in paths:
        body = await request(path.decode())
        assert len(body) > 1000
    assert (STATIC_DIR/'licenses.md').stat().st_size > 1000
    await app.state.service.boxie.close()
asyncio.run(run())
"""
    subprocess.run([str(python), "-I", "-c", probe], cwd=tmp_path, env=environment, check=True)


@pytest.mark.integration
@pytest.mark.parametrize("service", ["boxie", "cli-playground"])
@pytest.mark.parametrize("manifest", ["compose.yml", "compose.production.yml"])
@pytest.mark.parametrize("project_override", [False, True])
def test_resolved_compose_isolated_topology(
    tmp_path: Path, service: str, manifest: str, project_override: bool
) -> None:
    import os
    import subprocess

    values = {
        "BOXIE_DOCKER_GID": "999",
        "OBS_METRICS_NETWORK": "test-metrics",
        "CLIS_RELEASE_REVISION": "a" * 40,
        "PLAYGROUND_PROXY_IMAGE": "traefik:test",
        "BOXIE_RUNTIME_IMAGE": "boxie:test",
        "BOXIE_API_CONFIG": str(tmp_path / "api.json"),
        "BOXIE_POSTGRES_DSN_FILE": str(tmp_path / "postgres-dsn.secret"),
        "BOXIE_TEMPORAL_CA_FILE": str(tmp_path / "ca.pem"),
        "BOXIE_API_TEMPORAL_CERT_FILE": str(tmp_path / "api.pem"),
        "BOXIE_API_TEMPORAL_KEY_FILE": str(tmp_path / "api.key"),
        "BOXIE_ORCHESTRATOR_TEMPORAL_CERT_FILE": str(tmp_path / "orchestrator.pem"),
        "BOXIE_ORCHESTRATOR_TEMPORAL_KEY_FILE": str(tmp_path / "orchestrator.key"),
        "BOXIE_ORCHESTRATOR_CONFIG": str(tmp_path / "orchestrator.json"),
        "BOXIE_PROFILES_FILE": str(tmp_path / "profiles.json"),
        "BOXIE_ORCHESTRATOR_CREDENTIAL_FILE": str(tmp_path / "orchestrator.secret"),
        "BOXIE_CONSUMER_CREDENTIAL_FILE": str(tmp_path / "consumer.secret"),
        "BOXIE_DATA_DIR": str(tmp_path / "data"),
        "BOXIE_CONSUMER_NETWORK": "test-consumers",
        "BOXIE_CONTROL_NETWORK": "test-control",
        "PLAYGROUND_IMAGE": "playground:test",
        "PLAYGROUND_BOXIE_URL": "http://independent-boxie:8090",
        "PLAYGROUND_BOXIE_CREDENTIAL_FILE": str(tmp_path / "consumer.secret"),
        "PLAYGROUND_CONSUMER_NETWORK": "test-consumers",
        "PLAYGROUND_PROXY_NETWORK": "test-proxy",
        "PLAYGROUND_DATA_VOLUME": "test-product-data",
        "PLAYGROUND_CERTIFICATES_VOLUME": "test-certificates",
        "PLAYGROUND_DEPLOY_TOKEN": "test-token",
        "PLAYGROUND_DOMAIN": "play.example.com",
        "ACME_EMAIL": "test@example.com",
        "CLOUDFLARE_DNS_API_TOKEN": "test-dns-token",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COMPOSE_")
        and key not in {"BOXIE_PROJECT_NAME", "PLAYGROUND_PROJECT_NAME"}
    }
    if project_override:
        values.update(
            BOXIE_PROJECT_NAME="operator-boxie",
            PLAYGROUND_PROJECT_NAME="operator-playground",
        )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "tools" / service / "infra" / manifest),
            "config",
            "--format",
            "json",
        ],
        env={**environment, **values},
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(result.stdout)
    expected_project = (
        {"boxie": "operator-boxie", "cli-playground": "operator-playground"}[service]
        if project_override
        else ("clis-boxie" if service == "boxie" else "clis-playground")
        if manifest == "compose.production.yml"
        else service
    )
    assert config["name"] == expected_project
    for name, entry in config["services"].items():
        sockets = [v for v in entry.get("volumes", []) if v["target"] == "/var/run/docker.sock"]
        assert bool(sockets) == (service == "boxie" and name == "orchestrator")
        if service == "boxie":
            assert not entry.get("ports")
        if name == "orchestrator":
            mount = next(v for v in entry["volumes"] if v["target"] == values["BOXIE_DATA_DIR"])
            assert mount["source"] == mount["target"]
            assert set(entry["networks"]) == (
                {"control", "metrics"} if manifest == "compose.production.yml" else {"control"}
            )
        if name == "api":
            assert not any(v["target"] == values["BOXIE_DATA_DIR"] for v in entry["volumes"])
        if name == "gateway":
            assert not entry.get("depends_on")
            assert entry["environment"]["PLAYGROUND_BOXIE_URL"] == values["PLAYGROUND_BOXIE_URL"]
            assert not any("BOXIE_DATA" in v["source"] for v in entry["volumes"])
    assert all(net["external"] for net in config["networks"].values())
    assert all(volume["external"] for volume in config.get("volumes", {}).values())


def test_boxie_configuration_cli_is_product_independent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess
    import sys

    from boxie.bootstrap.api import load_configuration
    from boxie.bootstrap.config import OrchestratorConfig

    orchestrator_secret = tmp_path / "orchestrator.secret"
    consumer_secret = tmp_path / "consumer.secret"
    for path, value in [
        (orchestrator_secret, "test-orchestrator-credential"),
        (consumer_secret, "test-consumer-credential"),
    ]:
        path.write_text(value)
        path.chmod(0o600)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "boxie_infra.config",
            "--output",
            str(tmp_path),
            "--data-dir",
            str(tmp_path / "data"),
            "--installation-id",
            "00000000-0000-0000-0000-000000000001",
            "--host-id",
            "generic-host",
            "--consumer-id",
            "generic-consumer",
            "--orchestrator-credential",
            str(orchestrator_secret),
            "--consumer-credential",
            str(consumer_secret),
        ],
        check=True,
    )
    orchestrator = OrchestratorConfig.from_file(tmp_path / "orchestrator.json")
    monkeypatch.setenv("BOXIE_RELEASE", "c" * 40)
    api = load_configuration(tmp_path / "api.json")
    assert api.api.release == "c" * 40
    assert orchestrator.identity.host_id == "generic-host"
    generated = json.loads((tmp_path / "orchestrator.json").read_text())
    assert "max_sandboxes" not in generated
    generated["max_sandboxes"] = 4
    (tmp_path / "orchestrator.json").write_text(json.dumps(generated))
    with pytest.raises(TypeError, match="max_sandboxes"):
        OrchestratorConfig.from_file(tmp_path / "orchestrator.json")
    assert api.orchestrator_url == "http://orchestrator:8091"
    assert "playground" not in (tmp_path / "api.json").read_text().lower()
    assert "playground" not in (tmp_path / "orchestrator.json").read_text().lower()


@pytest.mark.parametrize("capacity", [1, 4, 8])
def test_orchestrator_configuration_rejects_unsupported_capacity(
    tmp_path: Path, capacity: int
) -> None:
    from boxie.bootstrap.config import OrchestratorConfig

    config = tmp_path / "orchestrator.json"
    config.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "profiles_file": "/etc/boxie/profiles.json",
                "credential_file": "/run/secrets/orchestrator",
                "identity": {"installation_id": str(uuid4()), "host_id": "test"},
                "max_sandboxes": capacity,
            }
        )
    )
    with pytest.raises(TypeError, match="max_sandboxes"):
        OrchestratorConfig.from_file(config)
