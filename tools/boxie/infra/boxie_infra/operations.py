"""Fixed owner operations on an explicitly reviewed checkout; no schema jobs or cloud API."""

import argparse
import asyncio
import base64
import fcntl
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from deploy_tool.manifests import json_bytes, object_map, read_json
from deploy_tool.prepare import canonical, private_write
from deploy_tool.remote import Activator
from deploy_tool.source import committed_files
from deploy_tool.transport import pinned_ssh, run_ssh

UNITS = {
    "postgres": ("postgres",),
    "temporal": ("temporal", "ui"),
    "observability": ("vector", "victorialogs", "prometheus", "grafana", "node-exporter"),
    "ingress": ("cloudflared",),
}
Run = Callable[[list[str], dict[str, str], Path, float], str]


def reviewed(repository: Path, revision: str, unit: str) -> dict[str, bytes]:
    if unit not in UNITS or re.fullmatch(r"[a-f0-9]{40}", revision) is None:
        raise ValueError("reviewed immutable revision and fixed owner unit required")
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(repository), *args], env=env, text=True, stderr=subprocess.DEVNULL
        ).strip()

    owner = "tools/boxie/infra/" + unit
    if git("rev-parse", "HEAD") != revision or git(
        "status", "--porcelain", "--untracked-files=all"
    ):
        raise ValueError("owner operation requires committed, unchanged source")
    paths = tuple(git("ls-tree", "-r", "--name-only", revision, "--", owner).splitlines())
    return committed_files(repository, revision, paths)


def native_readiness(
    unit: str,
    compose: list[str],
    services: tuple[str, ...],
    env: dict[str, str],
    repository: Path,
    run: Run,
) -> None:
    if unit == "postgres":
        return  # Compose --wait checks the authenticated psql healthcheck.
    if unit == "temporal":
        from temporalio.api.enums.v1 import NamespaceState
        from temporalio.api.workflowservice.v1 import DescribeNamespaceRequest
        from temporalio.client import Client
        from temporalio.service import TLSConfig

        certs = Path(env["TEMPORAL_OPERATOR_CERTS_DIR"])

        async def probe() -> None:
            async with asyncio.timeout(30):
                client = await Client.connect(
                    env["TEMPORAL_PROBE_ADDRESS"],
                    namespace="boxie",
                    tls=TLSConfig(
                        server_root_ca_cert=(certs / "frontend-ca.pem").read_bytes(),
                        domain="temporal",
                        client_cert=(certs / "operator.pem").read_bytes(),
                        client_private_key=(certs / "operator.key").read_bytes(),
                    ),
                )
                info = await client.workflow_service.describe_namespace(
                    DescribeNamespaceRequest(namespace="boxie"), timeout=timedelta(seconds=10)
                )
                if (
                    info.namespace_info.state != NamespaceState.NAMESPACE_STATE_REGISTERED
                    or info.config.workflow_execution_retention_ttl.seconds != 604800
                ):
                    raise ValueError(
                        "Temporal namespace readiness failed; run explicit bootstrap separately"
                    )

        asyncio.run(probe())
        return
    probes = {
        "vector": (9598, "/metrics"),
        "victorialogs": (9428, "/health"),
        "prometheus": (9090, "/-/ready"),
        "grafana": (3000, "/api/health"),
        "node-exporter": (9100, "/metrics"),
        "cloudflared": (2000, "/ready"),
    }
    for service in services:
        container = run(compose + ["ps", "-q", service], env, repository, 10).strip()
        info = json.loads(run(["docker", "inspect", container], env, repository, 10))[0]
        addresses = [
            v["IPAddress"]
            for v in info["NetworkSettings"]["Networks"].values()
            if v.get("IPAddress")
        ]
        if not addresses:
            raise ValueError("owner readiness requires its private bridge address")
        port, path = probes[service]
        end = time.monotonic() + 30
        while True:
            try:
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                with opener.open(f"http://{addresses[0]}:{port}{path}", timeout=2) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= end:
                raise ValueError("native owner readiness failed")
            time.sleep(0.2)


def activate_supporting(
    repository: Path, revision: str, unit: str, values: dict[str, object], *, run: Run | None = None
) -> dict[str, object]:
    files = reviewed(repository, revision, unit)
    owner = repository / "tools/boxie/infra" / unit
    env = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    for key, value in values.items():
        if (
            not re.fullmatch(r"[A-Z][A-Z0-9_]*", key)
            or key.startswith(("CLIS_", "COMPOSE_", "DOCKER_", "LD_", "PYTHON"))
            or key in {"PATH", "HOME", "ENV", "BASH_ENV", "SHELLOPTS"}
            or not isinstance(value, str)
            or "\0" in value
        ):
            raise ValueError("invalid owner environment")
        env[key] = value
    if unit == "temporal" and not env.get("TEMPORAL_PROBE_ADDRESS"):
        raise ValueError("Temporal requires its authenticated probe address")
    compose = [
        "docker",
        "compose",
        "--env-file",
        "/dev/null",
        "--project-directory",
        str(owner),
        "-p",
        "clis-boxie-" + unit,
        "-f",
        str(owner / "compose.yml"),
    ]
    if (owner / "compose.production.yml").exists():
        compose += ["-f", str(owner / "compose.production.yml")]
    if unit == "temporal" and values.get("BOXIE_MONITORING") == "true":
        compose += ["-f", str(owner / "compose.observability.yml")]
    adapter = Activator(repository, timeout=870, health_timeout=120)
    execute = run or adapter.run
    config = json.loads(execute(compose + ["config", "--format", "json"], env, repository, 30))
    services = UNITS[unit]
    images = {service: config["services"][service]["image"] for service in services}
    if any(not re.fullmatch(r".+@sha256:[a-f0-9]{64}", image) for image in images.values()):
        raise ValueError("native owner services require approved immutable images")
    with adapter.cancellation_signals():
        execute(compose + ["pull", *services], env, repository, 180)
        execute(
            compose
            + [
                "up",
                "-d",
                "--no-deps",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--wait-timeout",
                "120",
                *services,
            ],
            env,
            repository,
            180,
        )
        native_readiness(unit, compose, services, env, repository, execute)
        adapter.check_cancelled()
    return {
        "resolved_configuration_hash": hashlib.sha256(canonical(config)).hexdigest(),
        "unit": unit,
        "revision": revision,
        "services": list(services),
        "images": images,
        "configuration_hashes": {
            name: hashlib.sha256(data).hexdigest() for name, data in files.items()
        },
        "outcome": "activated",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--revision", required=True)
    parser.add_argument("--unit", choices=(*UNITS, "profiles"), required=True)
    parser.add_argument("--profile-source")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--stdin", action="store_true")
    parser.add_argument("--connection", type=Path)
    parser.add_argument("--identity", type=Path)
    args = parser.parse_args()
    try:
        request = (
            json_bytes(sys.stdin.buffer.read(1_500_001), limit=1_500_000)
            if args.stdin
            else read_json(args.request)
        )
        if not re.fullmatch(r"[a-f0-9]{40}", args.revision):
            raise ValueError("reviewed full revision required")
        if args.connection:
            if args.unit == "profiles":
                if not args.profile_source:
                    raise ValueError("committed profile source required")
                selected = committed_files(args.repository, args.revision, (args.profile_source,))
                request["content"] = base64.b64encode(selected[args.profile_source]).decode()
            if args.identity is None:
                raise ValueError("private SSH identity required")
            # This checkout/venv is explicitly installed by the operator first.
            # The command cannot fetch code or run schemas/provisioning remotely.
            checkout = "/opt/clis/operator-checkouts/" + args.revision
            remote = shlex.join(
                [
                    checkout + "/.venv/bin/python",
                    "-m",
                    "boxie_infra.operations",
                    "--repository",
                    checkout,
                    "--revision",
                    args.revision,
                    "--unit",
                    args.unit,
                    "--stdin",
                ]
            )
            with pinned_ssh(read_json(args.connection), args.identity, remote) as command:
                completed = run_ssh(command, canonical(request), 900)
            response = json_bytes(completed.stdout)
            if (
                completed.returncode
                or response.get("unit") != args.unit
                or response.get("revision") != args.revision
                or response.get("outcome") != "activated"
            ):
                raise ValueError("owner activation failed; inspect host state before retrying")
            print(json.dumps(response))
            return 0
        if args.unit == "profiles":
            from .profiles import main as profiles_main

            # Verify the host's reviewed checkout before invoking the fixed profile CLI.
            reviewed(args.repository, args.revision, "postgres")
            with tempfile.TemporaryDirectory() as directory:
                content = Path(directory) / "profiles.json"
                private_write(content, base64.b64decode(str(request.pop("content")), validate=True))
                argv = ["--content", str(content)]
                for key in (
                    "directory",
                    "installation-id",
                    "host-id",
                    "container",
                    "image",
                    "profile",
                    "command",
                ):
                    value = request.pop(key)
                    argv += ["--" + key, json.dumps(value) if key == "command" else str(value)]
                for option in ("project", "release-root"):
                    if option in request:
                        argv += ["--" + option, str(request.pop(option))]
                if request:
                    raise ValueError("unknown profile operation fields")
                if profiles_main(argv, quiet=True):
                    raise ValueError("profile acknowledgement failed")
            result: dict[str, Any] = {
                "unit": "profiles",
                "revision": args.revision,
                "outcome": "activated",
            }
        else:
            records = Path("/opt/clis/owner-operations")
            records.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd = os.open(
                records / f"{args.unit}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                result = activate_supporting(
                    args.repository,
                    args.revision,
                    args.unit,
                    object_map(request, "owner environment"),
                )
                private_write(records / f"{args.unit}-{time.time_ns()}.json", canonical(result))
        print(json.dumps({k: result[k] for k in ("unit", "revision", "outcome")}))
        return 0
    except BaseException:
        print("owner operation failed; inspect host state before retrying", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
