"""Independent service packaging and operator-owned topology contracts."""

import json
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_operations_manifest_keeps_docker_socket_out_of_operator_jobs() -> None:
    import yaml

    services = yaml.safe_load((ROOT / "packages/boxie/infra/compose.operations.yml").read_text())[
        "services"
    ]
    assert not any(
        "/var/run/docker.sock" in str(service.get("volumes", [])) for service in services.values()
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "dockerfile",
    [
        "packages/boxie/infra/Boxie.Dockerfile",
        "tools/cli-playground/infra/CliPlayground.Dockerfile",
    ],
)
def test_docker_context_excludes_generated_frontend_assets(tmp_path: Path, dockerfile: str) -> None:
    import subprocess

    context = tmp_path / "context"
    source = Path("tools/cli-playground/src/cli_playground")
    assets = context / source / "static"
    assets.mkdir(parents=True)
    (assets / "stale.js").write_text("stale local frontend")
    (context / source / "__init__.py").write_text("# backend source\n")
    own_source = context / "packages/boxie/src/boxie/__init__.py"
    own_source.parent.mkdir(parents=True)
    own_source.write_text("# Boxie source\n")
    recipe = context / dockerfile
    recipe.parent.mkdir(parents=True, exist_ok=True)
    recipe.write_text("FROM scratch\nCOPY . /\n")
    recipe.with_suffix(".Dockerfile.dockerignore").write_bytes(
        (ROOT / (dockerfile + ".dockerignore")).read_bytes()
    )
    output = tmp_path / "output"
    subprocess.run(
        [
            "docker",
            "build",
            "-f",
            str(recipe),
            "--output",
            f"type=local,dest={output}",
            str(context),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if dockerfile.startswith("packages/boxie/"):
        assert not (output / "tools").exists()
        assert (output / own_source.relative_to(context)).read_text() == "# Boxie source\n"
    else:
        assert (output / source / "__init__.py").read_text() == "# backend source\n"
        assert not (output / source / "static").exists()


@pytest.mark.integration
@pytest.mark.parametrize(
    "package,module,forbidden",
    [("boxie", "boxie", "cli_playground"), ("cli-playground", "cli_playground", "boxie")],
)
def test_real_isolated_installation(
    tmp_path: Path, package: str, module: str, forbidden: str
) -> None:
    import os
    import subprocess
    import sys

    service = ROOT / ("packages" if package == "boxie" else "tools") / package
    if package == "cli-playground":
        assert (service / "src/cli_playground/static/index.html").is_file(), (
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
for name in ({forbidden!r}, 'cli-playground', 'cli_playground_infra', 'boxie_infra', 'cli_playground_infra', 'boxie_test_support', 'pulumi', 'pytest'):
    assert importlib.util.find_spec(name) is None, name
"""
    if module == "boxie":
        probe += (
            "import boxie.bootstrap.api, boxie.bootstrap.supervisor, boxie.bootstrap.workers\n"
            "import boxie.observability.process, boxie.sandbox.execution.sandbox\n"
            "from boxie.bootstrap.api import ApiProcessConfig\n"
            "from boxie.observability.telemetry.logging import LogDispatcher\n"
            "from boxie.sandbox.execution.sandbox import SandboxExecution\n"
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
from cli_playground.app import create_app, STATIC_DIR
from cli_playground.config import Settings
secret = pathlib.Path('consumer.secret'); secret.write_text('isolated-consumer-secret'); secret.chmod(0o600)
dsn = pathlib.Path('postgres-dsn'); dsn.write_text('host=localhost dbname=cli_playground'); dsn.chmod(0o600)
from cli_playground.migrate import migration_config
from alembic import command
from io import StringIO
config = migration_config('postgresql://localhost/cli_playground'); config.output_buffer = StringIO()
command.upgrade(config, 'head', sql=True)
assert 'CREATE TABLE preview' in config.output_buffer.getvalue()
app = create_app(Settings(postgres_dsn_file=dsn.absolute(), data_dir=pathlib.Path.cwd()/'data', public_url='http://localhost', deploy_token='local-token', boxie_url='http://127.0.0.1:1', boxie_credential_file=secret.absolute()))
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
        "BOXIE_IMAGE": "boxie:test",
        "BOXIE_API_CONFIG": str(tmp_path / "api.json"),
        "BOXIE_POSTGRES_DSN_FILE": str(tmp_path / "postgres-dsn.secret"),
        "BOXIE_TEMPORAL_CA_FILE": str(tmp_path / "ca.pem"),
        "BOXIE_API_TEMPORAL_CERT_FILE": str(tmp_path / "api.pem"),
        "BOXIE_API_TEMPORAL_KEY_FILE": str(tmp_path / "api.key"),
        "BOXIE_SUPERVISOR_TEMPORAL_CERT_FILE": str(tmp_path / "supervisor.pem"),
        "BOXIE_SUPERVISOR_TEMPORAL_KEY_FILE": str(tmp_path / "supervisor.key"),
        "BOXIE_SUPERVISOR_CONFIG": str(tmp_path / "supervisor.json"),
        "BOXIE_PROFILES_FILE": str(tmp_path / "profiles.json"),
        "BOXIE_SUPERVISOR_CREDENTIAL_FILE": str(tmp_path / "supervisor.secret"),
        "BOXIE_CONSUMER_CREDENTIAL_FILE": str(tmp_path / "consumer.secret"),
        "BOXIE_DATA_DIR": str(tmp_path / "data"),
        "BOXIE_CONSUMER_NETWORK": "test-consumers",
        "BOXIE_CONTROL_NETWORK": "test-control",
        "CLI_PLAYGROUND_IMAGE": "cli-playground:test",
        "CLI_PLAYGROUND_POSTGRES_DSN_FILE": str(tmp_path / "playground-dsn.secret"),
        "CLI_PLAYGROUND_DATABASE_NETWORK": "test-database",
        "CLI_PLAYGROUND_BOXIE_URL": "http://independent-boxie:8090",
        "CLI_PLAYGROUND_BOXIE_CREDENTIAL_FILE": str(tmp_path / "consumer.secret"),
        "CLI_PLAYGROUND_CONSUMER_NETWORK": "test-consumers",
        "CLI_PLAYGROUND_PROXY_NETWORK": "test-proxy",
        "CLI_PLAYGROUND_DATA_VOLUME": "test-product-data",
        "CLI_PLAYGROUND_CERTIFICATES_VOLUME": "test-certificates",
        "CLI_PLAYGROUND_DEPLOY_TOKEN": "test-token",
        "CLI_PLAYGROUND_DOMAIN": "play.example.com",
        "ACME_EMAIL": "test@example.com",
        "CLOUDFLARE_DNS_API_TOKEN": "test-dns-token",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COMPOSE_")
        and key not in {"BOXIE_PROJECT_NAME", "CLI_PLAYGROUND_PROJECT_NAME"}
    }
    if project_override:
        values.update(
            BOXIE_PROJECT_NAME="operator-boxie",
            CLI_PLAYGROUND_PROJECT_NAME="operator-cli-playground",
        )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(
                ROOT
                / ("packages" if service == "boxie" else "tools")
                / service
                / "infra"
                / manifest
            ),
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
        {"boxie": "operator-boxie", "cli-playground": "operator-cli-playground"}[service]
        if project_override
        else service
    )
    assert config["name"] == expected_project
    for name, entry in config["services"].items():
        sockets = [v for v in entry.get("volumes", []) if v["target"] == "/var/run/docker.sock"]
        assert bool(sockets) == (service == "boxie" and name == "supervisor")
        if service == "boxie":
            assert not entry.get("ports")
        if name == "supervisor":
            mount = next(v for v in entry["volumes"] if v["target"] == values["BOXIE_DATA_DIR"])
            assert mount["source"] == mount["target"]
            assert set(entry["networks"]) == {"control"}
        if name == "api":
            assert not any(v["target"] == values["BOXIE_DATA_DIR"] for v in entry["volumes"])
        if name == "backend":
            assert set(entry["networks"]) == (
                {"consumer", "database", "proxy"}
                if manifest == "compose.production.yml"
                else {"consumer", "database"}
            )
            assert config["networks"]["database"]["name"] == "test-database"
            assert not entry.get("depends_on")
            assert (
                entry["environment"]["CLI_PLAYGROUND_BOXIE_URL"]
                == values["CLI_PLAYGROUND_BOXIE_URL"]
            )
            assert not any("BOXIE_DATA" in v["source"] for v in entry["volumes"])
    assert all(net["external"] for net in config["networks"].values())
    assert all(volume["external"] for volume in config.get("volumes", {}).values())


def test_boxie_configuration_cli_is_product_independent(tmp_path: Path) -> None:
    import subprocess
    import sys

    from boxie.bootstrap.api import load_configuration
    from boxie.bootstrap.config import SupervisorConfig

    supervisor_secret = tmp_path / "supervisor.secret"
    consumer_secret = tmp_path / "consumer.secret"
    for path, value in [
        (supervisor_secret, "test-supervisor-credential"),
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
            "--supervisor-credential",
            str(supervisor_secret),
            "--consumer-credential",
            str(consumer_secret),
        ],
        check=True,
    )
    supervisor = SupervisorConfig.from_file(tmp_path / "supervisor.json")
    api = load_configuration(tmp_path / "api.json")
    assert supervisor.identity.host_id == "generic-host"
    generated = json.loads((tmp_path / "supervisor.json").read_text())
    assert "max_sandboxes" not in generated
    generated["max_sandboxes"] = 4
    (tmp_path / "supervisor.json").write_text(json.dumps(generated))
    with pytest.raises(ValueError, match="extra_forbidden"):
        SupervisorConfig.from_file(tmp_path / "supervisor.json")
    assert api.supervisor_url == "http://supervisor:8091"
    assert "cli-playground" not in (tmp_path / "api.json").read_text().lower()
    assert "cli-playground" not in (tmp_path / "supervisor.json").read_text().lower()


@pytest.mark.parametrize("capacity", [1, 4, 8])
def test_supervisor_configuration_rejects_unsupported_capacity(
    tmp_path: Path, capacity: int
) -> None:
    from boxie.bootstrap.config import SupervisorConfig

    config = tmp_path / "supervisor.json"
    config.write_text(
        json.dumps(
            {
                "data_dir": str(tmp_path),
                "profiles_file": "/etc/boxie/profiles.json",
                "credential_file": "/run/secrets/supervisor",
                "identity": {"installation_id": str(uuid4()), "host_id": "test"},
                "max_sandboxes": capacity,
            }
        )
    )
    with pytest.raises(ValueError, match="extra_forbidden"):
        SupervisorConfig.from_file(config)


@pytest.mark.integration
def test_postgres_exposes_only_database_on_playground_network(tmp_path: Path) -> None:
    import os
    import subprocess

    result = subprocess.run(
        [
            "docker",
            "compose",
            "-f",
            str(ROOT / "packages/boxie/infra/postgres/compose.yml"),
            "config",
            "--format",
            "json",
        ],
        env={
            **os.environ,
            "BOXIE_CONTROL_NETWORK": "test-control",
            "POSTGRES_PASSWORD": "test",
            "BOXIE_DB_PASSWORD": "test",
            "TEMPORAL_DB_PASSWORD": "test",
            "VISIBILITY_DB_PASSWORD": "test",
        },
        capture_output=True,
        text=True,
        check=True,
    )
    config = json.loads(result.stdout)
    assert config["networks"]["playground"]["internal"] is True
    assert set(config["services"]) == {"postgres"}
    assert set(config["services"]["postgres"]["networks"]) == {"control", "playground"}
