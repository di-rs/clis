"""Two independent loopback servers, replacing only the agent's Docker port."""

import asyncio
import hashlib
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI

from boxie_test_support.agent import Clock, DockerDaemon, bundle, configuration, live, request

CONSUMER_AUTH = {"Authorization": "Bearer consumer-one-secret"}
FOREIGN_AUTH = {"Authorization": "Bearer consumer-two-secret"}


@asynccontextmanager
async def api_server(
    agent_url: str, agent_credential: bytes = b"agent-test-secret"
) -> AsyncIterator[httpx.AsyncClient]:
    from boxie.api.app import ApiConfig, create_app
    from boxie.application.direct_lifecycle import DirectLifecycle
    from boxie.sandbox.client import AgentClient

    async with AgentClient(agent_url, agent_credential, timeout=0.5) as agent:
        app = create_app(
            ApiConfig(
                "test-release", {"one": b"consumer-one-secret", "two": b"consumer-two-secret"}
            ),
            DirectLifecycle(agent),
            agent,
            agent,
        )
        async with serve(app) as url:
            async with httpx.AsyncClient(base_url=url, headers=CONSUMER_AUTH) as client:
                yield client


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[str]:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, log_level="error", ws="websockets-sansio", access_log=False)
    )
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        for _ in range(200):
            if server.started:
                break
            if task.done():
                await task
            await asyncio.sleep(0.01)
        assert server.started
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


@asynccontextmanager
async def stack(
    root: Path, **changes: Any
) -> AsyncIterator[tuple[httpx.AsyncClient, Any, DockerDaemon, Clock]]:
    daemon, clock = DockerDaemon(), Clock()
    async with live(configuration(root, **changes), daemon, clock) as (agent_http, agent):
        async with api_server(str(agent_http.base_url)) as client:
            assert client.base_url.port != agent_http.base_url.port
            yield client, agent, daemon, clock


async def artifact(client: httpx.AsyncClient) -> str:
    data, identifier = bundle(), str(uuid4())
    response = await client.put(
        f"/v1/artifacts/{identifier}",
        headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest()},
        content=data,
    )
    assert response.status_code == 201, response.text
    return identifier


async def create(client: httpx.AsyncClient, identifier: str, key: str = "first") -> str:
    response = await client.post(
        "/v1/sandboxes", json=request(identifier)["spec"], headers={"Idempotency-Key": key}
    )
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


async def ready(client: httpx.AsyncClient, identifier: str, state: str = "ready") -> dict[str, Any]:
    for _ in range(200):
        response = await client.get(f"/v1/sandboxes/{identifier}")
        assert response.status_code == 200, response.text
        value: dict[str, Any] = response.json()
        if value["state"] == state:
            return value
        await asyncio.sleep(0.01)
    raise AssertionError(value)


def terminal_url(client: httpx.AsyncClient, identifier: str) -> str:
    return str(client.base_url).replace("http:", "ws:") + f"/v1/sandboxes/{identifier}/terminal"
