"""One real upload connects logs from three independently configured processes."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

from .helpers import binary_bundle, elf_binary

CHILD = """
import asyncio, sys
from pathlib import Path
from types import SimpleNamespace
from contextlib import asynccontextmanager
from uuid import uuid4
import uvicorn
role, port, upstream, root = sys.argv[1:]
async def run():
    if role == "cli-playground":
        from cli_playground.app import create_app
        from cli_playground.config import Settings
        from cli_playground.boxie_client import HttpBoxieClient
        def factory():
            return create_app(Settings(data_dir=Path(root), public_url="http://127.0.0.1:"+port, deploy_token="deployment-secret", boxie_url=upstream, boxie_credential_file=Path(root)/"unused"), boxie=HttpBoxieClient(upstream,"consumer-one-secret"))
        app = factory
    else:
        from boxie.observability.logging import bootstrap_logging, LogSettings
        bootstrap_logging(LogSettings(service="boxie",role=role))
        if role == "supervisor":
            from boxie.sandbox.transport.server import create_supervisor_app
            from boxie_test_support.artifacts import Artifacts
            from boxie_test_support.supervisor import configuration
            config=configuration(Path(root))
            @asynccontextmanager
            async def lifespan(app): yield
            async def ready(): return {"ready":True}
            app=create_supervisor_app(SimpleNamespace(config=config,boot_id=uuid4()),Artifacts(config.data_dir/"artifacts"),credential=b"supervisor-test-secret",readiness=ready,lifespan=lifespan)
        else:
            from boxie.api.app import create_app, ApiConfig
            from boxie.sandbox.transport.client import SupervisorClient
            class Lifecycle:
                async def ready(self): return True
            client=SupervisorClient(upstream,b"supervisor-test-secret")
            app=create_app(ApiConfig(release="test",consumers={"one":b"consumer-one-secret"}),Lifecycle(),client,client)
    server=uvicorn.Server(uvicorn.Config(app, factory=role=="cli-playground",host="127.0.0.1",port=int(port),access_log=False,log_config=None))
    await server.serve()
asyncio.run(run())
"""


def test_response_id_finds_three_service_logs(tmp_path: Path) -> None:
    processes: list[tuple[str, subprocess.Popen[str]]] = []
    upstream = "http://127.0.0.1:1"
    identity = ""
    try:
        for role in ("supervisor", "api", "cli-playground"):
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            process = subprocess.Popen(
                [sys.executable, "-c", CHILD, role, str(port), upstream, str(tmp_path / role)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={
                    **{
                        key: value
                        for key, value in os.environ.items()
                        if not key.startswith("CLI_PLAYGROUND_")
                    },
                    "CLI_PLAYGROUND_DATABASE_URL": os.environ["CLI_PLAYGROUND_DATABASE_URL"],
                },
            )
            processes.append((role, process))
            upstream = f"http://127.0.0.1:{port}"
            with httpx.Client(base_url=upstream, timeout=1) as client:
                for _ in range(200):
                    assert process.poll() is None, process.communicate()
                    try:
                        if client.get(
                            "/readyz",
                            headers={
                                "Authorization": "Bearer supervisor-test-secret"
                                if role == "supervisor"
                                else "Bearer consumer-one-secret"
                            },
                        ).status_code in (200, 404):
                            break
                    except httpx.TransportError:
                        pass
                    time.sleep(0.01)
                else:
                    raise AssertionError("service startup timed out")
        with httpx.Client(base_url=upstream, timeout=10) as client:
            response = client.post(
                "/api/environments",
                params={"sha": "a" * 40, "pr": 123},
                headers={"Authorization": "Bearer deployment-secret"},
                content=binary_bundle({"echor": elf_binary()}),
            )
            assert response.status_code == 201, response.text
            identity = response.headers["x-request-id"]
    finally:
        for role, process in reversed(processes):
            process.terminate()
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode in (0, -15), stdout + stderr
            events = [json.loads(line) for line in stdout.splitlines() if line]
            if identity:
                assert any(event.get("request_id") == identity for event in events), (
                    role,
                    events,
                    stderr,
                )
