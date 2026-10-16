"""Loopback product/API/orchestrator harness; only the Docker daemon is substituted."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI
from playground.app import create_app
from playground.boxie_client import HttpBoxieClient
from playground.config import Settings
from playground.service import PlaygroundService


@dataclass
class Network:
    api: httpx.AsyncClient
    daemon: Any
    orchestrator: Any
    config: Any
    clock: Any


@dataclass
class Product:
    http: httpx.AsyncClient
    app: FastAPI
    service: PlaygroundService


@asynccontextmanager
async def boxie_network(root: Path) -> AsyncIterator[Network]:
    from boxie_test_support.terminal_network import network

    async with network(root, shell="/opt/playground/shell.sh") as result:
        yield Network(result.api, result.daemon, result.orchestrator, result.config, result.clock)


@asynccontextmanager
async def product_server(root: Path, api_url: str, *, port: int = 0) -> AsyncIterator[Product]:
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    url = f"http://127.0.0.1:{sock.getsockname()[1]}"
    settings = Settings(
        data_dir=root,
        public_url=url,
        deploy_token="test-deployment-token",
        boxie_url=api_url,
        boxie_profile="shell",
        boxie_credential_file=root / "secret",
    )
    app = create_app(settings, boxie=HttpBoxieClient(api_url, "consumer-one-secret"))
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
        async with httpx.AsyncClient(base_url=url) as client:
            yield Product(client, app, app.state.service)
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, 5)
        sock.close()


async def publish(product: Product, sha: str, pr: int = 123) -> httpx.Response:
    from .helpers import binary_bundle, elf_binary

    return await product.http.post(
        "/api/environments",
        params={"sha": sha, "pr": pr},
        headers={"Authorization": "Bearer test-deployment-token"},
        content=binary_bundle({"echor": elf_binary(sha.encode())}),
    )


async def admit(product: Product, identifier: str, sha: str) -> dict[str, str]:
    response = await product.http.post(
        f"/api/environments/{identifier}/sessions",
        headers={"Origin": str(product.http.base_url).rstrip("/")},
        json={"revision": sha},
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def socket_url(product: Product, identifier: str, binding: dict[str, str]) -> str:
    return (
        str(product.http.base_url).rstrip("/").replace("http:", "ws:")
        + f"/ws/{identifier}?session_id={binding['id']}&revision={binding['revision']}"
    )


@asynccontextmanager
async def serve(app: FastAPI) -> AsyncIterator[str]:
    from boxie_test_support.api import serve as serve_peer

    async with serve_peer(app) as url:
        yield str(url)
