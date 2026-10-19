"""Isolated production-image deployment harness. No dev server and no silent skips.

Run with ``uv run --locked python packages/boxie/tests/run_integration.py``.
Only this harness's randomly named projects and volumes are removed.
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

import yaml

ROOT = Path(__file__).parents[3]


def command(args: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        args, env=env, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=240
    )
    if result.returncode:
        raise RuntimeError(f"command failed: {args[:4]}\n{result.stdout}")
    return result.stdout.strip()


class CommandRunner(Protocol):
    def __call__(self, args: list[str], *, env: dict[str, str] | None = None) -> str: ...


class ProductionStack:
    def __init__(self, root: Path, *, command_runner: CommandRunner | None = None) -> None:
        self._command_runner = command_runner or command
        self.root = root
        self.name = "boxie-integration-" + uuid4().hex[:12]
        self.postgres = self.name + "-postgres"
        self.temporal = self.name + "-temporal"
        self.network_id: str | None = None
        self.env = dict(os.environ)
        self.env.update(
            {
                key: secrets.token_hex(24)
                for key in (
                    "POSTGRES_PASSWORD",
                    "BOXIE_DB_PASSWORD",
                    "TEMPORAL_DB_PASSWORD",
                    "VISIBILITY_DB_PASSWORD",
                )
            }
        )
        self.env.update(
            BOXIE_CONTROL_NETWORK=self.name,
            TEMPORAL_CERTS_DIR=str(root / "server"),
            TEMPORAL_OPERATOR_CERTS_DIR=str(root / "operator"),
            TEMPORAL_UI_CERTS_DIR=str(root / "ui"),
        )
        # Compose resolves the operations-only image even when that service is
        # unused. This fixture executes bootstrap with the installed host SDK;
        # LifecycleStack replaces this with its built runtime for application jobs.
        self.env.setdefault("BOXIE_IMAGE", "boxie:lifecycle")
        self.address = ""
        self.ui_address = ""
        self.pg_port = ""
        self.evidence: dict[str, Any] = {}

    def command(self, args: list[str], *, env: dict[str, str] | None = None) -> str:
        return self._command_runner(args, env=self.env if env is None else env)

    def compose(self, package: str, *args: str) -> str:
        directory = (
            ROOT / "packages/boxie/infra/temporal"
            if package == "temporal"
            else ROOT / "packages/boxie/infra/postgres"
        )
        return self.command(
            [
                "docker",
                "compose",
                "--project-name",
                getattr(self, package),
                "--file",
                str(directory / "compose.yml"),
                "--file",
                str(self.root / f"{package}-override.yml"),
                *args,
            ],
            env=self.env,
        )

    def certificates(self) -> None:
        ca = self.root / "ca"
        ca.mkdir()
        for directory in ("server", "operator", "ui", "clients"):
            (self.root / directory).mkdir()
        for name in ("frontend", "internode", "client", "untrusted"):
            self.command(
                [
                    "openssl",
                    "req",
                    "-x509",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-days",
                    "2",
                    "-subj",
                    f"/CN=Boxie test {name} CA",
                    "-keyout",
                    str(ca / f"{name}.key"),
                    "-out",
                    str(ca / f"{name}.pem"),
                ]
            )
        for name, issuer, san, usage, directory in (
            ("frontend", "frontend", "temporal", "serverAuth", "server"),
            ("internode", "internode", "temporal-internode", "serverAuth,clientAuth", "server"),
            ("operator", "client", "operator", "clientAuth", "operator"),
            ("ui-client", "client", "ui", "clientAuth", "ui"),
            ("boxie-api", "client", "boxie-api", "clientAuth", "clients"),
            ("boxie-supervisor", "client", "boxie-supervisor", "clientAuth", "clients"),
            ("untrusted", "untrusted", "untrusted", "clientAuth", "clients"),
        ):
            target = self.root / directory
            key, cert = target / f"{name}.key", target / f"{name}.pem"
            csr, ext = ca / f"{name}.csr", ca / f"{name}.ext"
            self.command(
                [
                    "openssl",
                    "req",
                    "-newkey",
                    "rsa:2048",
                    "-nodes",
                    "-subj",
                    f"/CN={name}",
                    "-keyout",
                    str(key),
                    "-out",
                    str(csr),
                ]
            )
            ext.write_text(
                f"subjectAltName=DNS:{san}\nextendedKeyUsage={usage}\nbasicConstraints=CA:FALSE\n"
            )
            self.command(
                [
                    "openssl",
                    "x509",
                    "-req",
                    "-in",
                    str(csr),
                    "-CA",
                    str(ca / f"{issuer}.pem"),
                    "-CAkey",
                    str(ca / f"{issuer}.key"),
                    "-CAcreateserial",
                    "-days",
                    "2",
                    "-extfile",
                    str(ext),
                    "-out",
                    str(cert),
                ]
            )
            # Host tests and lifecycle API/worker share the fixture owner's UID.
            # The unchanged Temporal server/operator/UI fixture images use UID 1000.
            key.chmod(0o600 if directory == "clients" else 0o644)
        for directory in ("server", "operator", "ui", "clients"):
            shutil.copy(ca / "frontend.pem", self.root / directory / "frontend-ca.pem")
        for name in ("internode", "client"):
            shutil.copy(ca / f"{name}.pem", self.root / "server" / f"{name}-ca.pem")

    def write_overrides(self) -> None:
        # The host Temporal client survives process kill/start in native scenarios.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            temporal_port = reservation.getsockname()[1]
        (self.root / "postgres-override.yml").write_text(
            yaml.safe_dump(
                {
                    "services": {"postgres": {"ports": ["127.0.0.1::5432"]}},
                }
            )
        )
        (self.root / "temporal-override.yml").write_text(
            yaml.safe_dump(
                {
                    "services": {
                        "temporal": {"ports": [f"127.0.0.1:{temporal_port}:7233"]},
                        "ui": {"ports": ["127.0.0.1::8080"]},
                    }
                }
            )
        )

        # Dependency diagnostics remain local and bounded during journal faults.
        for name, roles in (
            ("postgres", ("postgres",)),
            ("temporal", ("temporal", "ui", "schema", "bootstrap")),
        ):
            path = self.root / f"{name}-override.yml"
            data = yaml.safe_load(path.read_text())
            for role in roles:
                data["services"].setdefault(role, {})["logging"] = {
                    "driver": "local",
                    "options": {
                        "mode": "non-blocking",
                        "max-buffer-size": "4m",
                        "max-size": "10m",
                        "max-file": "3",
                    },
                }
            path.write_text(yaml.safe_dump(data).replace("logging:", "logging: !override"))

    def start(self) -> None:
        self.certificates()
        self.write_overrides()
        # The harness owns this external bridge separately from both projects.
        # Host-side SQL/Temporal clients require a non-internal disposable bridge.
        self.network_id = self.command(
            ["docker", "network", "create", "--label", f"boxie.integration={self.name}", self.name]
        )
        self.compose("postgres", "up", "--detach", "--wait", "--wait-timeout", "90")
        self.pg_port = self.compose("postgres", "port", "postgres", "5432").rsplit(":", 1)[1]
        self.compose("temporal", "run", "--rm", "schema", "setup")
        # Exercise upgrade path independently from first-install setup.
        self.compose("temporal", "run", "--rm", "schema", "update")
        self.compose("temporal", "up", "--detach", "temporal", "ui")
        self.address = self.compose("temporal", "port", "temporal", "7233")
        self.ui_address = "http://" + self.compose("temporal", "port", "ui", "8080")
        last_error = ""
        for _ in range(10):
            try:
                self.bootstrap()
                break
            except RuntimeError as error:
                last_error = str(error)
                (self.root / "bootstrap-error.log").write_text(last_error)
                time.sleep(1)
        else:
            raise RuntimeError(
                "Temporal bootstrap failed\n"
                + last_error
                + "\n"
                + self.compose("temporal", "logs", "--tail", "100")
            )
        self.evidence = {
            "server": self.command(
                ["docker", "version", "--format", "{{.Server.Version}} {{.Server.Arch}}"]
            ),
            "bootstrap_sdk": version("temporalio"),
            "images": json.loads(
                (ROOT / "packages/boxie/infra/temporal/versions.json").read_text()
            ),
            "schema": "fresh setup and explicit update succeeded for distinct databases",
            "namespace": "bootstrap succeeded and checked seven-day retention",
        }
        (self.root / "evidence.json").write_text(json.dumps(self.evidence, indent=2))

    def bootstrap(self) -> str:
        return self.command(
            [
                sys.executable,
                "-m",
                "boxie.bootstrap.temporal",
                "--address",
                self.address,
                "--ca-file",
                str(self.root / "operator/frontend-ca.pem"),
                "--cert-file",
                str(self.root / "operator/operator.pem"),
                "--key-file",
                str(self.root / "operator/operator.key"),
            ]
        )

    def close(self) -> None:
        failures: list[str] = []
        for package in ("temporal", "postgres"):
            if (self.root / f"{package}-override.yml").exists():
                try:
                    self.compose(package, "down", "--volumes", "--remove-orphans")
                except Exception as error:
                    failures.append(str(error))
        if self.network_id is not None:
            try:
                item = json.loads(self.command(["docker", "network", "inspect", self.network_id]))[
                    0
                ]
                if (
                    item["Id"] != self.network_id
                    or item["Name"] != self.name
                    or item.get("Labels", {}).get("boxie.integration") != self.name
                ):
                    raise RuntimeError("refusing cleanup of foreign control network")
                self.command(["docker", "network", "rm", self.network_id])
                self.network_id = None
            except Exception as error:
                failures.append(str(error))
        if failures:
            raise RuntimeError("; ".join(failures))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", choices=["runsc"])
    parser.add_argument("--browser", action="store_true")
    parser.add_argument("pytest_args", nargs="*")
    args = parser.parse_args()
    if args.browser and not args.runtime:
        parser.error("--browser requires --runtime runsc")
    if args.browser:
        os.environ["BOXIE_BROWSER"] = "1"
    selected = (
        ["packages/boxie/tests/integration/test_lifecycle.py"]
        if args.runtime
        else [
            "packages/boxie/tests/test_postgres.py",
            "packages/boxie/tests/test_temporal_deployment.py",
            "packages/boxie/tests/test_migrations.py",
            "packages/boxie/tests/test_rename_state.py",
            "packages/boxie/tests/test_observability_lifecycle.py",
            "packages/boxie/tests/test_observability_logging.py",
        ]
    )
    raise SystemExit(
        subprocess.call(
            [
                "uv",
                "run",
                "--locked",
                "pytest",
                "-m",
                "integration",
                *selected,
                *args.pytest_args,
            ]
        )
    )


if __name__ == "__main__":
    main()
