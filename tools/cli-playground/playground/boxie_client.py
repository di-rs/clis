"""Product-owned adapter for Boxie's private consumer HTTP/WebSocket API."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import aiohttp
from pydantic import AwareDatetime, BaseModel, ConfigDict, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedOK, WebSocketException


class BoxieUnavailable(RuntimeError):
    """A bounded private service call failed; credentials never enter public errors."""


SandboxState = Literal["accepted", "provisioning", "ready", "terminating", "terminated", "failed"]
TerminationReason = Literal[
    "requested",
    "expired",
    "idle",
    "disconnected",
    "output_limit",
    "exited",
    "provisioning_failed",
    "agent_restart",
    "agent_shutdown",
]


class SandboxView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    state: SandboxState
    created_at: AwareDatetime
    absolute_deadline: AwareDatetime
    reason: TerminationReason | None
    cleanup_pending: bool


class SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: UUID
    profile_id: str
    command: tuple[str, ...] = ("/opt/playground/shell.sh",)
    env: dict[str, str] = {"TERM": "xterm-256color"}
    policy: dict[str, int] = {"max_seconds": 3600, "idle_seconds": 600, "disconnect_seconds": 120}


class TerminalPort(Protocol):
    async def receive(self) -> bytes | str | None: ...
    async def send(self, message: str) -> None: ...


class BoxieClient(Protocol):
    async def upload(self, identifier: UUID, digest: str, archive: Path) -> None: ...
    async def retire(self, identifier: UUID) -> None: ...
    async def create(self, key: str, spec: SandboxRequest) -> SandboxView: ...
    async def get(self, identifier: UUID) -> SandboxView: ...
    async def terminate(self, identifier: UUID) -> SandboxView: ...
    def terminal(self, identifier: UUID) -> AbstractAsyncContextManager[TerminalPort]: ...
    async def close(self) -> None: ...


class NoRedirectConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return exc


class HttpTerminal:
    def __init__(self, socket: ClientConnection) -> None:
        self.socket = socket

    async def receive(self) -> bytes | str | None:
        try:
            message = await self.socket.recv()
        except ConnectionClosedOK:
            return None
        if isinstance(message, bytes):
            return message
        try:
            value = json.loads(message)
        except ValueError as error:
            raise BoxieUnavailable("invalid terminal status") from error
        if not isinstance(value, dict) or set(value) != {"type", "reason"}:
            raise BoxieUnavailable("invalid terminal status")
        reasons = {
            "requested",
            "idle",
            "disconnected",
            "output_limit",
            "exited",
            "provisioning_failed",
            "agent_restart",
            "agent_shutdown",
            "expired",
        }
        if (
            value["type"] != "status"
            or not isinstance(value["reason"], str)
            or value["reason"] not in reasons
        ):
            raise BoxieUnavailable("invalid terminal status")
        return str(value["reason"])

    async def send(self, message: str) -> None:
        async with asyncio.timeout(10):
            await self.socket.send(message)


class HttpBoxieClient:
    def __init__(self, url: str, credential: str) -> None:
        self.url = url.rstrip("/")
        self.headers = {"Authorization": f"Bearer {credential}"}
        self._session: aiohttp.ClientSession | None = None

    def _http(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = aiohttp.ClientSession(
                headers=self.headers, timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> bytes:
        try:
            async with self._http().request(
                method, self.url + path, data=body, headers=headers, allow_redirects=False
            ) as response:
                data = await bounded_response(response)
                if response.status not in {200, 201, 202, 204} or len(data) > 65536:
                    raise BoxieUnavailable("sandbox service unavailable")
                return data
        except (aiohttp.ClientError, TimeoutError, ValueError) as error:
            raise BoxieUnavailable("sandbox service unavailable") from error

    async def upload(self, identifier: UUID, digest: str, archive: Path) -> None:
        async def chunks() -> AsyncIterator[bytes]:
            with archive.open("rb") as stream:
                while data := stream.read(65536):
                    yield data
                    await asyncio.sleep(0)

        try:
            async with self._http().put(
                self.url + f"/v1/artifacts/{identifier}",
                data=chunks(),
                headers={"X-Content-SHA256": digest},
                allow_redirects=False,
            ) as response:
                if response.status not in {200, 201}:
                    raise BoxieUnavailable("artifact service unavailable")
                await bounded_response(response)
        except (aiohttp.ClientError, TimeoutError) as error:
            raise BoxieUnavailable("artifact service unavailable") from error

    async def retire(self, identifier: UUID) -> None:
        await self._request("DELETE", f"/v1/artifacts/{identifier}")

    async def create(self, key: str, spec: SandboxRequest) -> SandboxView:
        return parse_sandbox(
            await self._request(
                "POST",
                "/v1/sandboxes",
                body=spec.model_dump_json().encode(),
                headers={"Idempotency-Key": key, "Content-Type": "application/json"},
            )
        )

    async def get(self, identifier: UUID) -> SandboxView:
        return parse_sandbox(await self._request("GET", f"/v1/sandboxes/{identifier}"))

    async def terminate(self, identifier: UUID) -> SandboxView:
        return parse_sandbox(await self._request("DELETE", f"/v1/sandboxes/{identifier}"))

    @asynccontextmanager
    async def terminal(self, identifier: UUID) -> AsyncIterator[TerminalPort]:
        try:
            async with NoRedirectConnect(
                self.url.replace("http:", "ws:").replace("https:", "wss:")
                + f"/v1/sandboxes/{identifier}/terminal",
                additional_headers=self.headers,
                proxy=None,
                max_size=65536,
                open_timeout=10,
                close_timeout=2,
            ) as socket:
                yield HttpTerminal(socket)
        except (WebSocketException, OSError, TimeoutError) as error:
            raise BoxieUnavailable("terminal unavailable") from error

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None


async def bounded_response(response: aiohttp.ClientResponse) -> bytes:
    data = bytearray()
    async for chunk in response.content.iter_chunked(8192):
        data.extend(chunk)
        if len(data) > 65536:
            raise BoxieUnavailable("invalid service response")
    return bytes(data)


def parse_sandbox(data: bytes) -> SandboxView:
    try:
        return SandboxView.model_validate_json(data)
    except ValidationError as error:
        raise BoxieUnavailable("invalid sandbox response") from error
