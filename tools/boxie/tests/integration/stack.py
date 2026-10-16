"""Native lifecycle assembly, independent process controls and owned teardown."""

import json
import os
import secrets
import socket
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
from boxie.persistence.database import database
from boxie.persistence.migrate import migrate
from run_integration import ROOT, ProductionStack, command
from sqlalchemy.sql import Select


class LifecycleStack(ProductionStack):
    def __init__(self, root: Path) -> None:
        super().__init__(root)
        self.installation = str(uuid4())
        self.host = self.name
        self.runtime = self.name + "-runtime"
        self.runtime_started = False
        self.api_url = self.product_url = self.dsn = ""
        self.consumer = secrets.token_hex(24)
        self.deploy = secrets.token_hex(24)
        self.binary = Path(os.environ.get("BOXIE_NATIVE_BINARY", ROOT / "target/release/echor"))
        self.env.update(
            BOXIE_TEST_ROOT=str(root),
            # Bind-mounted private files and writable storage belong to this runner.
            # UID 0 with cap_drop ALL cannot bypass another UID's mode-0600 files.
            BOXIE_TEST_USER=f"{os.getuid()}:{os.getgid()}",
            BOXIE_TEST_ORCHESTRATOR_SCRIPT=str(Path(__file__).with_name("orchestrator_fixture.py")),
            BOXIE_TEST_FAULTS_SCRIPT=str(Path(__file__).with_name("faults.py")),
            BOXIE_RUNTIME_IMAGE=os.environ.get("BOXIE_RUNTIME_IMAGE", "boxie:lifecycle"),
            PLAYGROUND_IMAGE=os.environ.get("PLAYGROUND_IMAGE", "cli-playground:lifecycle"),
            BOXIE_DATA_DIR=str(root / "data"),
            BOXIE_ORCHESTRATOR_CONFIG=str(root / "orchestrator.json"),
            BOXIE_API_CONFIG=str(root / "api.json"),
            BOXIE_PROFILES_FILE=str(root / "profiles.json"),
            BOXIE_ORCHESTRATOR_CREDENTIAL_FILE=str(root / "orchestrator.secret"),
            BOXIE_CONSUMER_CREDENTIAL_FILE=str(root / "consumer.secret"),
            BOXIE_POSTGRES_DSN_FILE=str(root / "postgres-dsn"),
            BOXIE_TEMPORAL_CA_FILE=str(root / "clients/frontend-ca.pem"),
            BOXIE_API_TEMPORAL_CERT_FILE=str(root / "clients/boxie-api.pem"),
            BOXIE_API_TEMPORAL_KEY_FILE=str(root / "clients/boxie-api.key"),
            BOXIE_ORCHESTRATOR_TEMPORAL_CERT_FILE=str(root / "clients/boxie-orchestrator.pem"),
            BOXIE_ORCHESTRATOR_TEMPORAL_KEY_FILE=str(root / "clients/boxie-orchestrator.key"),
            BOXIE_CONSUMER_NETWORK=self.name + "-consumer",
            PLAYGROUND_DEPLOY_TOKEN=self.deploy,
            PLAYGROUND_PUBLIC_URL="http://127.0.0.1:1",
        )

    def runtime_command(self, *args: str) -> str:
        return command(
            [
                "docker",
                "compose",
                "--project-name",
                self.runtime,
                "--file",
                str(ROOT / "tools/boxie/infra/compose.test.yml"),
                *args,
            ],
            env=self.env,
        )

    def start(self) -> None:
        info = json.loads(command(["docker", "info", "--format", "{{json .}} "]))
        if info["Architecture"] not in ("x86_64", "amd64") or "runsc" not in info["Runtimes"]:
            raise RuntimeError(
                "native lifecycle requires Linux amd64 Docker with configured runsc; no fallback"
            )
        if not self.binary.is_file():
            raise RuntimeError("native echor binary is required (BOXIE_NATIVE_BINARY)")
        super().start()
        for name, value in (
            ("orchestrator.secret", secrets.token_hex(24)),
            ("consumer.secret", self.consumer),
        ):
            path = self.root / name
            path.write_text(value)
            path.chmod(0o600)
        self.dsn = f"host=127.0.0.1 port={self.pg_port} dbname=boxie user=boxie password={self.env['BOXIE_DB_PASSWORD']} sslmode=disable"
        for name, host in (("postgres-dsn", "postgres"), ("orchestrator-dsn", "faults")):
            path = self.root / name
            path.write_text(
                f"host={host} dbname=boxie user=boxie password={self.env['BOXIE_DB_PASSWORD']} sslmode=disable"
            )
            path.chmod(0o600)
        migrate(self.dsn, ROOT / "tools/boxie/src/boxie/persistence/migrations")
        # Compose's nested profiles bind must not create a root-owned directory.
        (self.root / "data/profiles").mkdir(parents=True, mode=0o700)
        (self.root / "faults").mkdir()
        profiles = json.loads(
            (ROOT / "tools/cli-playground/infra/profiles/playground.json").read_text()
        )
        profiles.update(json.loads((ROOT / "tools/boxie/infra/profiles.example.json").read_text()))
        image = command(
            [
                "docker",
                "image",
                "inspect",
                os.environ.get("BOXIE_SANDBOX_IMAGE", "clis-playground-sandbox:lifecycle"),
                "--format",
                "{{.Id}}",
            ]
        )
        for profile in profiles.values():
            profile["image_digest"] = image
        (self.root / "profiles.json").write_text(json.dumps(profiles))
        command(
            [
                "uv",
                "run",
                "--locked",
                "python",
                "-m",
                "boxie_infra.config",
                "--output",
                str(self.root),
                "--data-dir",
                str(self.root / "data"),
                "--installation-id",
                self.installation,
                "--host-id",
                self.host,
                "--consumer-id",
                "smoke",
            ],
            env=self.env,
        )
        # Explicit host bindings survive Docker kill/start; an omitted published
        # port is reassigned on restart and invalidates long-lived test clients.
        with socket.socket() as api_reservation, socket.socket() as reservation:
            api_reservation.bind(("127.0.0.1", 0))
            self.env["BOXIE_TEST_API_PORT"] = str(api_reservation.getsockname()[1])
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
        self.env["BOXIE_TEST_PRODUCT_PORT"] = str(port)
        self.product_url = f"http://127.0.0.1:{port}"
        self.env["PLAYGROUND_PUBLIC_URL"] = self.product_url
        self.runtime_started = True
        self.runtime_command("up", "--detach", "faults", "orchestrator", "api", "playground")
        self.api_url = "http://" + self.runtime_command("port", "api", "8090")
        self.product_url = "http://" + self.runtime_command("port", "playground", "8000")
        self.env["PLAYGROUND_PUBLIC_URL"] = self.product_url
        self.wait_ready()
        self.evidence.update(
            native_runtime="runsc",
            architecture=info["Architecture"],
            installation=self.installation,
            host=self.host,
        )
        (self.root / "evidence.json").write_text(json.dumps(self.evidence, indent=2))

    def wait_ready(self) -> None:
        last = "no response"
        with httpx.Client(timeout=2, trust_env=False) as client:
            for _ in range(120):
                try:
                    response = client.get(
                        self.api_url + "/readyz",
                        headers={"Authorization": "Bearer " + self.consumer},
                    )
                    last = f"HTTP {response.status_code}: {response.text[:1024]}"
                    if response.status_code == 200:
                        return
                except httpx.HTTPError as error:
                    last = f"transport {type(error).__name__}"
                time.sleep(0.5)
        raise RuntimeError(
            f"lifecycle API {self.api_url} did not become ready: {last}; "
            f"published={self.runtime_command('port', 'api', '8090')}\n"
            + self.runtime_command("logs", "--tail", "80")
        )

    def orchestrator_boot(self) -> UUID:
        """Read the running orchestrator's authenticated identity after startup reconciliation."""
        return UUID(
            self.runtime_command(
                "exec",
                "-T",
                "orchestrator",
                "python",
                "-c",
                """
import time
from pathlib import Path
import httpx
credential = Path('/run/secrets/orchestrator').read_text().strip()
with httpx.Client(headers={'Authorization': 'Bearer ' + credential}, timeout=1, trust_env=False) as client:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            response = client.get('http://127.0.0.1:8091/v1/identity')
            response.raise_for_status()
            print(response.json()['boot_id'])
            break
        except httpx.HTTPError:
            time.sleep(0.2)
    else:
        raise RuntimeError('restarted orchestrator identity unavailable')
""",
            )
        )

    def fault(self, name: str, enabled: bool) -> None:
        if name not in {"hold_create", "hold_commit", "fail_delete"}:
            raise ValueError("unknown owned fault")
        path = self.root / "faults" / name
        if enabled:
            path.touch()
        else:
            path.unlink(missing_ok=True)

    def process(self, service: str, action: str) -> None:
        if service not in {"api", "orchestrator", "temporal"} or action not in {
            "kill",
            "start",
        }:
            raise ValueError("unknown owned process action")
        if service == "temporal":
            self.compose("temporal", action, service)
        else:
            self.runtime_command(action, service)

    async def rows(self, statement: Select[Any]) -> list[dict[str, Any]]:
        async with database(self.dsn) as sessions, sessions() as session:
            connection = await session.connection()
            return [dict(row) for row in (await connection.execute(statement)).mappings()]

    def children(self) -> list[str]:
        ids = command(
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=boxie.installation=" + self.installation,
                "--filter",
                "label=boxie.host=" + self.host,
            ]
        ).split()
        owned = []
        for identifier in ids:
            info = json.loads(command(["docker", "inspect", identifier]))[0]
            labels = info["Config"]["Labels"]
            if (
                labels.get("boxie.installation") == self.installation
                and labels.get("boxie.host") == self.host
                and labels.get("boxie.sandbox")
                and labels.get("boxie.generation")
                and labels.get("boxie.consumer")
                and labels.get("boxie.boot")
            ):
                try:
                    sandbox = UUID(labels["boxie.sandbox"])
                    UUID(labels["boxie.boot"])
                    UUID(labels["boxie.artifact"])
                    generation = int(labels["boxie.generation"])
                except KeyError, ValueError:
                    continue
                expected = f"/boxie-{UUID(self.installation).hex}-{sandbox.hex}-{generation}"
                if (
                    generation > 0
                    and info["Name"] == expected
                    and labels["boxie.consumer"] == "smoke"
                ):
                    owned.append(info["Id"])
        return owned

    def clean_children(self) -> None:
        failures: list[str] = []
        for _ in range(2):
            for identifier in self.children():
                try:
                    command(["docker", "rm", "--force", identifier])
                except Exception as error:
                    failures.append(str(error))
            time.sleep(0.2)
        if self.children():
            raise RuntimeError("owned sandboxes remain after cleanup: " + "; ".join(failures))

    def close(self) -> None:
        if not self.runtime_started:
            super().close()
            return
        errors = []
        for name in ("hold_create", "hold_commit", "fail_delete"):
            self.fault(name, False)
        for operation in (
            lambda: (self.root / "runtime.log").write_text(
                self.runtime_command("logs", "--no-color")
            ),
            lambda: self.runtime_command("down", "--volumes", "--remove-orphans"),
            self.clean_children,
            super().close,
        ):
            try:
                operation()
            except Exception as error:
                errors.append(str(error))
        if errors:
            (self.root / "cleanup-errors.log").write_text("\n".join(errors))
            raise RuntimeError("owned fixture cleanup failed; see " + str(self.root))
