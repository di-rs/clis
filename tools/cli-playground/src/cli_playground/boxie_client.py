"""Product-owned adapter for Boxie's private consumer HTTP/WebSocket API."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

import aiohttp
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosedOK, WebSocketException

from cli_playground.terminal_protocol import TerminalEvent
from cli_playground.terminal_protocol import response as terminal_response


class BoxieUnavailable(RuntimeError):
    """A bounded private service call failed; credentials never enter public errors."""


class TerminalWriteClosed(BoxieUnavailable):
    """Boxie closed normally while its final response may still be buffered."""


SandboxState = Literal["accepted", "provisioning", "ready", "terminating", "terminated", "failed"]
TerminationReason = Literal[
    "requested",
    "expired",
    "idle",
    "disconnected",
    "output_limit",
    "exited",
    "provisioning_failed",
    "supervisor_restart",
    "supervisor_shutdown",
    "control_plane_lost",
]


class SandboxView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    state: SandboxState
    created_at: AwareDatetime
    absolute_deadline: AwareDatetime
    reason: TerminationReason | None
    cleanup_pending: bool


class ArtifactReply(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    retired: bool


class SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_id: UUID
    profile_id: str
    command: tuple[str, ...] = ("/opt/cli-playground/shell.sh",)
    env: dict[str, str] = {"TERM": "xterm-256color"}
    policy: dict[str, int] = {"max_seconds": 3600, "idle_seconds": 600, "disconnect_seconds": 120}


class TerminalPort(Protocol):
    async def receive(self) -> bytes | TerminalEvent | None: ...
    async def send(self, message: str) -> None: ...


class BoxieClient(Protocol):
    async def upload(self, identifier: UUID, digest: str, archive: Path) -> None: ...
    async def retire(self, identifier: UUID) -> None: ...
    async def create(self, key: str, spec: SandboxRequest) -> SandboxView: ...
    async def get(self, identifier: UUID, previous: SandboxView | None = None) -> SandboxView: ...
    async def terminate(
        self, identifier: UUID, previous: SandboxView | None = None
    ) -> SandboxView: ...
    def terminal(self, identifier: UUID) -> AbstractAsyncContextManager[TerminalPort]: ...
    async def close(self) -> None: ...


class NoRedirectConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return exc


class HttpTerminal:
    def __init__(self, socket: ClientConnection) -> None:
        self.socket = socket

    async def receive(self) -> bytes | TerminalEvent | None:
        try:
            message = await self.socket.recv()
        except ConnectionClosedOK:
            return None
        if isinstance(message, bytes):
            return message
        try:
            return terminal_response.validate_json(message)
        except ValueError as error:
            raise BoxieUnavailable("invalid terminal response") from error

    async def send(self, message: str) -> None:
        try:
            async with asyncio.timeout(10):
                await self.socket.send(message)
        except ConnectionClosedOK:
            raise TerminalWriteClosed("terminal upstream closed") from None


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
                try:
                    reply = ArtifactReply.model_validate_json(await bounded_response(response))
                except ValidationError as error:
                    raise BoxieUnavailable("invalid artifact response") from error
                if reply.id != identifier or reply.sha256 != digest or reply.retired:
                    raise BoxieUnavailable("invalid artifact response")
        except (aiohttp.ClientError, TimeoutError) as error:
            raise BoxieUnavailable("artifact service unavailable") from error

    async def retire(self, identifier: UUID) -> None:
        await self._request("DELETE", f"/v1/artifacts/{identifier}")

    async def create(self, key: str, spec: SandboxRequest) -> SandboxView:
        view = parse_sandbox(
            await self._request(
                "POST",
                "/v1/sandboxes",
                body=spec.model_dump_json().encode(),
                headers={"Idempotency-Key": key, "Content-Type": "application/json"},
            )
        )
        validate_sandbox(view, max_seconds=spec.policy["max_seconds"])
        return view

    async def _sandbox(
        self, method: str, identifier: UUID, previous: SandboxView | None
    ) -> SandboxView:
        view = parse_sandbox(await self._request(method, f"/v1/sandboxes/{identifier}"))
        validate_sandbox(view, previous, identifier=identifier)
        return view

    async def get(self, identifier: UUID, previous: SandboxView | None = None) -> SandboxView:
        return await self._sandbox("GET", identifier, previous)

    async def terminate(self, identifier: UUID, previous: SandboxView | None = None) -> SandboxView:
        return await self._sandbox("DELETE", identifier, previous)

    @asynccontextmanager
    async def terminal(self, identifier: UUID) -> AsyncIterator[TerminalPort]:
        try:
            async with NoRedirectConnect(
                self.url.replace("http:", "ws:").replace("https:", "wss:")
                + f"/v1/sandboxes/{identifier}/terminal",
                additional_headers=self.headers,
                proxy=None,
                max_size=65536,
                max_queue=4,
                write_limit=65536,
                compression=None,
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
        view = SandboxView.model_validate_json(data)
        validate_sandbox(view)
        return view
    except ValidationError as error:
        raise BoxieUnavailable("invalid sandbox response") from error


def validate_sandbox(
    view: SandboxView,
    previous: SandboxView | None = None,
    *,
    identifier: UUID | None = None,
    max_seconds: int = 3600,
) -> None:
    lifetime = (view.absolute_deadline - view.created_at).total_seconds()
    if (
        not 0 < lifetime <= max_seconds
        or (identifier is not None and view.id != identifier)
        or (
            previous is not None
            and (
                view.id != previous.id
                or view.created_at != previous.created_at
                or view.absolute_deadline != previous.absolute_deadline
            )
        )
    ):
        raise BoxieUnavailable("invalid sandbox response")
