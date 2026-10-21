"""Application-owned browser proof against the independently usable Boxie stack."""

import json
import os
import secrets
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from integration.stack import LifecycleStack

from .helpers import binary_bundle

ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.integration


def wait_for_request_log(
    runtime_command: Callable[[str, str, str, str], str],
    service: str,
    request_id: str,
    *,
    timeout: float = 10,
) -> None:
    deadline = time.monotonic() + timeout
    while True:
        logs = runtime_command("logs", "--no-color", "--no-log-prefix", service)
        events = [json.loads(line) for line in logs.splitlines() if line.startswith("{")]
        if any(event.get("request_id") == request_id for event in events):
            return
        assert time.monotonic() < deadline, service
        time.sleep(0.2)


def test_native_application_and_browser(tmp_path: Path) -> None:
    binary = Path(os.environ.get("BOXIE_NATIVE_BINARY", ROOT / "target/release/echor"))
    if not binary.is_file():
        raise RuntimeError("native echor binary is required (BOXIE_NATIVE_BINARY)")
    token = secrets.token_hex(24)
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    stack = LifecycleStack(
        tmp_path,
        runtime_overlay=ROOT / "tools/cli-playground/infra/compose.test.yml",
        runtime_services=("faults", "supervisor", "api"),
    )
    stack.env.update(
        CLI_PLAYGROUND_DATABASE_NETWORK=stack.postgres + "_playground",
        CLI_PLAYGROUND_IMAGE=os.environ.get("CLI_PLAYGROUND_IMAGE", "cli-playground:lifecycle"),
        BOXIE_TEST_PRODUCT_PORT=str(port),
        CLI_PLAYGROUND_PUBLIC_URL=origin,
        CLI_PLAYGROUND_DEPLOY_TOKEN=token,
    )
    try:
        stack.start()
        # Provision the application's database separately from Boxie's lifecycle schema.
        import psycopg

        from .helpers import migrate

        password = secrets.token_hex(32)
        with psycopg.connect(
            f"host=127.0.0.1 port={stack.pg_port} user=postgres dbname=postgres password={stack.env['POSTGRES_PASSWORD']}",
            autocommit=True,
        ) as connection:
            from psycopg import sql

            connection.execute(
                sql.SQL("CREATE ROLE cli_playground LOGIN PASSWORD {}").format(
                    sql.Literal(password)
                )
            )
            connection.execute("CREATE DATABASE cli_playground OWNER cli_playground")
            connection.execute("REVOKE CONNECT ON DATABASE cli_playground FROM PUBLIC")
        migrate(
            f"postgresql+psycopg://cli_playground:{password}@127.0.0.1:{stack.pg_port}/cli_playground"
        )
        secret = stack.root / "playground-dsn"
        secret.write_text(f"postgresql+psycopg://cli_playground:{password}@postgres/cli_playground")
        secret.chmod(0o600)
        stack.runtime_command("up", "--detach", "cli-playground")
        with httpx.Client(base_url=origin, timeout=30) as public:
            response = public.post(
                "/api/environments",
                params={"sha": "c" * 40, "pr": 987654},
                headers={"Authorization": "Bearer " + token},
                content=binary_bundle({binary.name: binary.read_bytes()}),
            )
            assert response.status_code == 201, response.text
            request_id = response.headers["x-request-id"]
        for service in ("cli-playground", "api", "supervisor"):
            wait_for_request_log(stack.runtime_command, service, request_id)
        subprocess.run(
            [
                "uv",
                "run",
                "python",
                "-m",
                "cli_playground_infra.smoke",
                "--boxie-url",
                stack.api_url,
                "--credential-file",
                str(stack.root / "consumer.secret"),
                "--binary",
                str(binary),
            ],
            check=True,
            timeout=180,
        )
        subprocess.run(
            ["pnpm", "exec", "playwright", "test", "--config", "playwright.native.config.ts"],
            cwd=ROOT / "tools/cli-playground/frontend",
            env={
                **os.environ,
                "BOXIE_NATIVE_ORIGIN": origin,
                "BOXIE_NATIVE_BINARY": str(binary),
                "BOXIE_NATIVE_DEPLOY_TOKEN": token,
            },
            check=True,
            timeout=180,
        )
        for _ in range(120):
            if not stack.children():
                break
            time.sleep(0.5)
        assert not stack.children()
    finally:
        stack.close()
