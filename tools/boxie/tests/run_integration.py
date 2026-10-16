"""Isolated production-image deployment harness. No dev server and no silent skips.

Run with ``uv run --locked python tools/boxie/tests/run_integration.py``.
Only this harness's randomly named projects and volumes are removed.
"""

import argparse
import json
import os
import secrets
import shutil
import socket
import subprocess
import time
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
    def __init__(self, root: Path, *, command_runner: CommandRunner = command) -> None:
        self._command_runner = command_runner
        self.root = root
        self.name = "boxie-integration-" + uuid4().hex[:12]
        self.platform = self.name + "-platform"
        self.temporal = self.name + "-temporal"
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
            OBS_METRICS_NETWORK=self.name + "-metrics",
            OBS_GRAFANA_ORIGIN_NETWORK=self.name + "-grafana",
            OBS_TEMPORAL_UI_ORIGIN_NETWORK=self.name + "-temporal-ui",
            TEMPORAL_CERTS_DIR=str(root / "server"),
            TEMPORAL_OPERATOR_CERTS_DIR=str(root / "operator"),
            TEMPORAL_UI_CERTS_DIR=str(root / "ui"),
        )
        self.build_network: str | None = None
        self.address = ""
        self.ui_address = ""
        self.pg_port = ""
        self.evidence: dict[str, Any] = {}
        self.shared_networks: list[str] = []

    def command(self, args: list[str], *, env: dict[str, str] | None = None) -> str:
        return self._command_runner(args, env=self.env if env is None else env)

    def compose(self, package: str, *args: str) -> str:
        return self.command(
            [
                "docker",
                "compose",
                "--project-name",
                getattr(self, package),
                "--file",
                str(ROOT / "infra" / package / "compose.yml"),
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
            ("boxie-worker", "client", "boxie-worker", "clientAuth", "clients"),
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
        # These non-journal service tests intentionally use Docker local logging.
        # The host Temporal client survives process kill/start in native scenarios.
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            temporal_port = reservation.getsockname()[1]
        (self.root / "platform-override.yml").write_text(
            yaml.safe_dump(
                {
                    "services": {
                        "postgres": {
                            "ports": ["127.0.0.1::5432"],
                            "logging": {
                                "driver": "local",
                                "options": {"mode": "non-blocking", "max-buffer-size": "4m"},
                            },
                        }
                    },
                    "networks": {"control": {"internal": False}},
                }
            )
        )
        (self.root / "temporal-override.yml").write_text(
            yaml.safe_dump(
                {
                    "services": {
                        "schema": {
                            "logging": {
                                "driver": "local",
                                "options": {"max-size": "10m", "max-file": "3"},
                            }
                        },
                        "bootstrap": {
                            **(
                                {"build": {"network": self.build_network}}
                                if self.build_network
                                else {}
                            ),
                            "logging": {
                                "driver": "local",
                                "options": {"max-size": "10m", "max-file": "3"},
                            },
                        },
                        "temporal": {
                            "ports": [f"127.0.0.1:{temporal_port}:7233"],
                            "logging": {
                                "driver": "local",
                                "options": {"mode": "non-blocking", "max-buffer-size": "4m"},
                            },
                        },
                        "ui": {
                            "ports": ["127.0.0.1::8080"],
                            "logging": {
                                "driver": "local",
                                "options": {"mode": "non-blocking", "max-buffer-size": "4m"},
                            },
                        },
                    }
                }
            )
        )
        for name in ("platform-override.yml", "temporal-override.yml"):
            path = self.root / name
            path.write_text(path.read_text().replace("logging:", "logging: !override"))

    def start(self) -> None:
        self.certificates()
        self.write_overrides()
        self.compose("platform", "up", "--detach", "--wait", "--wait-timeout", "90")
        # Compose omits unused declarations. The fixture explicitly owns these
        # bridges, just as production platform bootstrap must own its handles.
        for variable in (
            "OBS_METRICS_NETWORK",
            "OBS_GRAFANA_ORIGIN_NETWORK",
            "OBS_TEMPORAL_UI_ORIGIN_NETWORK",
        ):
            network_id = self.command(
                [
                    "docker",
                    "network",
                    "create",
                    "--internal",
                    "--label",
                    f"io.boxie.test.owner={self.name}",
                    self.env[variable],
                ]
            )
            self.shared_networks.append(network_id)
            network = json.loads(self.command(["docker", "network", "inspect", network_id]))[0]
            if not network["Internal"] or network["Labels"].get("io.boxie.test.owner") != self.name:
                raise RuntimeError("fixture network ownership/internal verification failed")
        self.pg_port = self.compose("platform", "port", "postgres", "5432").rsplit(":", 1)[1]
        self.compose("temporal", "build", "bootstrap")
        self.compose(
            "temporal", "run", "--rm", "--entrypoint", "/usr/local/bin/jq", "bootstrap", "--version"
        )
        self.compose("temporal", "run", "--rm", "schema", "setup")
        # Exercise upgrade path independently from first-install setup.
        self.compose("temporal", "run", "--rm", "schema", "update")
        self.compose("temporal", "up", "--detach", "temporal", "ui")
        self.address = self.compose("temporal", "port", "temporal", "7233")
        self.ui_address = "http://" + self.compose("temporal", "port", "ui", "8080")
        last_error = ""
        for _ in range(10):
            try:
                self.compose("temporal", "run", "--rm", "bootstrap")
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
        self.compose("temporal", "run", "--rm", "bootstrap")
        self.evidence = {
            "server": self.command(
                ["docker", "version", "--format", "{{.Server.Version}} {{.Server.Arch}}"]
            ),
            "bootstrap_local_image_id": self.command(
                ["docker", "image", "inspect", self.temporal + "-bootstrap", "--format", "{{.Id}}"]
            ),
            "images": json.loads((ROOT / "infra/temporal/versions.json").read_text()),
            "schema": "fresh setup and explicit update succeeded for distinct databases",
            "namespace": "bootstrap succeeded twice and checked seven-day retention",
        }
        (self.root / "evidence.json").write_text(json.dumps(self.evidence, indent=2))

    def close(self) -> None:
        failures: list[str] = []
        for package in ("temporal", "platform"):
            if (self.root / f"{package}-override.yml").exists():
                try:
                    self.compose(package, "down", "--volumes", "--remove-orphans")
                except Exception as error:
                    failures.append(str(error))
        for network_id in self.shared_networks:
            try:
                remaining = self.command(
                    [
                        "docker",
                        "network",
                        "ls",
                        "--filter",
                        f"id={network_id}",
                        "--format",
                        "{{.ID}}",
                    ]
                )
                if remaining:
                    network = json.loads(
                        self.command(["docker", "network", "inspect", network_id])
                    )[0]
                    if network["Labels"].get("io.boxie.test.owner") != self.name:
                        raise RuntimeError("refusing cleanup of foreign fixture network")
                    self.command(["docker", "network", "rm", network_id])
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
        ["tools/boxie/tests/integration/test_lifecycle.py"]
        if args.runtime
        else ["tools/boxie/tests/test_postgres.py", "tools/boxie/tests/test_temporal_deployment.py"]
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
