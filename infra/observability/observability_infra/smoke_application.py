"""Real lifecycle fixture adapter and production terminal client for fault intervals."""

import asyncio
import hashlib
import importlib
import io
import json
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import psycopg
import yaml
from boxie_infra.terminal import Session
from psycopg.rows import dict_row
from websockets.asyncio.client import connect

from observability_infra.smoke_runtime import Commands, evidence

REPO = Path(__file__).resolve().parents[3]


def stack_at(root: Path, commands: Commands) -> Any:
    # Reuse concrete assembly, never pytest fixtures or replaced application clients.
    sys.path.insert(0, str(REPO / "tools/boxie/tests"))
    cls = importlib.import_module("integration.stack").LifecycleStack
    stack = cls(
        root / "application",
        command_runner=commands.run,
        runtime_overlay=root / "application-overlay.yml",
    )
    stack.capture_teardown_logs = False
    stack.runtime_services = ("faults", "agent", "api", "worker")
    state = root / "application-private.json"
    if state.exists():
        data = json.loads(state.read_text())
        for name, value in data.items():
            setattr(stack, name, value)
    stack.env["DOCKER_HOST"] = "unix://" + str(root / "docker.sock")
    # Compose 2.38 Bake omits the allow flag for the explicit host build network.
    # Use its direct BuildKit path within this fixture, without changing runtime networks.
    stack.env["COMPOSE_BAKE"] = "false"
    return stack


def save_stack(root: Path, stack: Any) -> None:
    # Explicit private allowlist, never part of uploaded evidence.
    values = {
        name: getattr(stack, name)
        for name in (
            "name",
            "platform",
            "temporal",
            "installation",
            "host",
            "runtime",
            "env",
            "runtime_started",
            "api_url",
            "product_url",
            "dsn",
            "consumer",
            "deploy",
            "address",
            "ui_address",
            "pg_port",
            "shared_networks",
        )
    }
    path = root / "application-private.json"
    path.touch(mode=0o600, exist_ok=True)
    evidence(path, values)
    path.chmod(0o600)


def application_overlay(root: Path) -> None:
    services: dict[str, Any] = {}
    for role in ("api", "worker", "agent"):
        services[role] = {
            "logging": {
                "driver": "journald",
                "options": {
                    "mode": "non-blocking",
                    "max-buffer-size": "4m",
                    "tag": "boxie-" + role,
                },
            },
            "mem_limit": "512m",
            "memswap_limit": "512m",
            "pids_limit": 256,
        }
    services["api"].update(
        command=["python", "/fixture/pipe_api.py"],
        volumes=[
            f"{root}/control:/fixture-control",
            f"{REPO}/infra/observability/observability_infra/smoke_pipe_api.py:/fixture/pipe_api.py:ro",
        ],
        ports=["127.0.0.1::9101"],
    )
    services["agent"]["command"] = ["boxie-agent", "--config", "/etc/boxie/agent.json"]
    services["faults"] = {"volumes": [f"{root}/docker.sock:/var/run/docker.sock"]}
    text = yaml.safe_dump({"services": services}, sort_keys=False)
    text = text.replace("logging:", "logging: !override")
    (root / "application-overlay.yml").write_text(text)


class Application:
    def __init__(self, root: Path, commands: Commands) -> None:
        self.root, self.commands, self.deadline = root, commands, commands.deadline
        self.stack = stack_at(root, commands)
        self.artifacts: list[str] = []
        self.sandboxes: list[str] = []
        self.attachment_orders: dict[str, int] = {}
        self.sentinels = (
            self.stack.consumer,
            "artifact-private-" + uuid4().hex,
            "terminal-private-" + uuid4().hex,
            "cookie-private-" + uuid4().hex,
        )
        self.headers = {
            "Authorization": "Bearer " + self.stack.consumer,
            "Cookie": "test=" + self.sentinels[3],
        }
        self.client = httpx.AsyncClient(
            base_url=self.stack.api_url,
            headers=self.headers,
            timeout=2,
            trust_env=False,
            follow_redirects=False,
        )

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        async with asyncio.timeout(self.deadline.remaining(restoring=self.commands.restoring)):
            response = await self.client.request(method, path, **kwargs)
            response.raise_for_status()
            return response

    async def provision(self, *, failed: bool = False) -> str:
        identifier = str(uuid4())
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr("message.txt", self.sentinels[1])
        payload = archive.getvalue()
        self.artifacts.append(identifier)  # Register identity before submission.
        await self.request(
            "PUT",
            "/v1/artifacts/" + identifier,
            content=payload,
            headers={"X-Content-SHA256": hashlib.sha256(payload).hexdigest()},
        )
        response = await self.request(
            "POST",
            "/v1/sandboxes",
            headers={"Idempotency-Key": str(uuid4())},
            json={
                "artifact_id": identifier,
                "profile_id": "smoke",
                "command": ["/bin/sh"],
                "env": {},
                "policy": {"max_seconds": 3600, "idle_seconds": 600, "disconnect_seconds": 120},
            },
        )
        sandbox = str(response.json()["id"])
        self.sandboxes.append(sandbox)
        await self.state(sandbox, "failed" if failed else "ready")
        if not failed:
            ids = await asyncio.to_thread(self.stack.children)
            own = [
                json.loads(
                    await asyncio.to_thread(
                        self.commands.run, ["docker", "inspect", item], env=self.stack.env
                    )
                )[0]
                for item in ids
            ]
            selected = [
                item for item in own if item["Config"]["Labels"]["boxie.sandbox"] == sandbox
            ]
            if (
                len(selected) != 1
                or selected[0]["HostConfig"]["Runtime"] != "runsc"
                or selected[0]["HostConfig"]["LogConfig"]["Type"] != "none"
            ):
                raise RuntimeError("real owned runsc sandbox required")
        return sandbox

    async def state(self, sandbox: str, expected: str) -> dict[str, Any]:
        while True:
            response = await self.request("GET", "/v1/sandboxes/" + sandbox)
            view: dict[str, Any] = response.json()
            if view["state"] == expected and (expected == "ready" or not view["cleanup_pending"]):
                return view
            await asyncio.sleep(0.1)

    async def progress(self, sandbox: str) -> dict[str, float | bool]:
        started = time.monotonic()
        await self.request("GET", "/v1/sandboxes/" + sandbox)
        http_seconds = time.monotonic() - started
        endpoint = self.stack.api_url.replace("http://", "ws://")
        # Reserve before connecting: a failed response may still have attached.
        order = self.attachment_orders.get(sandbox, 0) + 1
        self.attachment_orders[sandbox] = order
        async with asyncio.timeout(min(12, self.deadline.remaining())):
            async with connect(
                endpoint + f"/v1/sandboxes/{sandbox}/terminal",
                additional_headers=self.headers,
                proxy=None,
                open_timeout=3,
                close_timeout=1,
            ) as connection:
                session = Session(connection.recv, connection.send)
                await session.start(attachment_order=order)
                token = uuid4().hex
                await session.write(
                    (
                        f"cat /opt/artifact/message.txt; printf '%s%s' '{token[:16]}' '{token[16:]}'\n"
                    ).encode()
                )
                result = bytearray()
                while token.encode() not in result:
                    data = await session.read()
                    if not data:
                        raise RuntimeError("real terminal ended during fault")
                    result.extend(data)
                    if len(result) > 65536:
                        raise RuntimeError("terminal evidence overflow")
                if self.sentinels[1].encode() not in result:
                    raise RuntimeError("real artifact not observed")
                # Split the privacy marker too: echoed input cannot prove output.
                marker = self.sentinels[2]
                await session.write((f"printf '%s%s' '{marker[:16]}' '{marker[16:]}'\n").encode())
                result.clear()
                while marker.encode() not in result:
                    data = await session.read()
                    if not data or len(result) + len(data) > 65536:
                        raise RuntimeError("terminal privacy roundtrip failed")
                    result.extend(data)
        return {
            "http_seconds": http_seconds,
            "roundtrip_seconds": time.monotonic() - started,
            "terminal_marker_observed": True,
        }

    def committed(self, sandbox: str) -> dict[str, Any]:
        self.deadline.remaining()
        with psycopg.connect(
            self.stack.dsn,
            connect_timeout=2,
            options="-c statement_timeout=2000",
            row_factory=dict_row,
        ) as conn:
            row = conn.execute(
                "SELECT state, cleanup_pending FROM sandboxes WHERE id=%s", (sandbox,)
            ).fetchone()
            if row is None:
                raise RuntimeError("committed sandbox absent")
            return row

    async def close(self) -> None:
        for sandbox in self.sandboxes:
            await self.request("DELETE", "/v1/sandboxes/" + sandbox)
            while True:
                view = (await self.request("GET", "/v1/sandboxes/" + sandbox)).json()
                if view["state"] in {"terminated", "failed"} and not view["cleanup_pending"]:
                    break
                await asyncio.sleep(0.1)
        for artifact in self.artifacts:
            await self.request("DELETE", "/v1/artifacts/" + artifact)
        await self.client.aclose()
