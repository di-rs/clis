"""Two independent loopback servers, replacing only the supervisor's Docker port."""

import asyncio
import hashlib
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import httpx
import uvicorn
from fastapi import FastAPI

from boxie_test_support.supervisor import bundle

CONSUMER_AUTH = {"Authorization": "Bearer consumer-one-secret"}
FOREIGN_AUTH = {"Authorization": "Bearer consumer-two-secret"}


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
        "/v1/sandboxes", json={"artifact_id": identifier}, headers={"Idempotency-Key": key}
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
