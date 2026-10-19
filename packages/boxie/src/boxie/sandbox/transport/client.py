"""Bounded authenticated private-orchestrator network adapter; no Docker or disk access."""

import asyncio
import logging
from collections.abc import AsyncIterable, AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit
from uuid import UUID

import httpx
from pydantic import ValidationError
from websockets.exceptions import (
    InvalidHandshake,
    InvalidStatus,
)

from boxie.application.ports import TerminalConnection
from boxie.contracts.consumer import ArtifactWire, spec_wire
from boxie.contracts.execution import (
    ControlRequest,
    IdentityWire,
    ReadyWire,
    SnapshotWire,
)
from boxie.domain.errors import BoxieError
from boxie.domain.models import (
    ArtifactPutResult,
    OrchestratorIdentity,
    Principal,
    ProfileSnapshot,
    SandboxSpec,
)
from boxie.sandbox.transport.terminal import (
    NoRedirectConnect,
    OrchestratorTerminal,
    dependency_error,
)

ERRORS = {
    404: "resource not found",
    409: "request conflicts with existing resource",
    413: "request too large",
    422: "invalid request",
    429: "sandbox capacity reached",
}


class OrchestratorClient:
    def __init__(
        self,
        base_url: str,
        credential: bytes,
        *,
        timeout: float = 20,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        url = urlsplit(base_url)
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.username is not None
            or url.password is not None
            or url.query
            or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("orchestrator URL must be an HTTP origin")
        if not 0 < timeout <= 60:
            raise ValueError("invalid orchestrator timeout")
        self._base_url = base_url.rstrip("/")
        self._ws_url = urlunsplit(
            ("wss" if url.scheme == "https" else "ws", url.netloc, "", "", "")
        )
        self._authorization = "Bearer " + credential.decode("ascii")
        self._timeout = timeout
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers={"Authorization": self._authorization},
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
            transport=transport,
        )

    async def __aenter__(self) -> OrchestratorClient:
        return self

    async def __aexit__(
        self,
        _type: type[BaseException] | None,
        _value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self._http.aclose()

    async def _request(self, method: str, path: str, **kwargs: Any) -> tuple[int, bytes]:
        try:
            async with asyncio.timeout(self._timeout):
                async with self._http.stream(method, path, **kwargs) as response:
                    if response.status_code in ERRORS:
                        raise BoxieError(response.status_code, ERRORS[response.status_code])
                    if response.status_code not in (200, 201, 204):
                        raise dependency_error()
                    body = bytearray()
                    async for chunk in response.aiter_bytes(65536):
                        if len(body) + len(chunk) > 65536:
                            raise dependency_error()
                        body.extend(chunk)
                    return response.status_code, bytes(body)
        except httpx.HTTPError, TimeoutError:
            raise dependency_error() from None

    async def ready(self) -> bool:
        try:
            _status, body = await self._request("GET", "/readyz")
            readiness = ReadyWire.model_validate_json(body)
            ready = bool(readiness.dependencies) and all(readiness.dependencies.values())
        except BoxieError, ValidationError:
            ready = False
        if not ready:
            logging.getLogger(__name__).warning("Orchestrator readiness probe failed")
        return ready

    async def put(
        self, principal: Principal, artifact_id: UUID, sha256: str, chunks: AsyncIterable[bytes]
    ) -> ArtifactPutResult:
        async def bounded() -> AsyncIterator[bytes]:
            count = 0
            async for chunk in chunks:
                count += len(chunk)
                if count > 512 * 1024 * 1024:
                    raise BoxieError(413, "compressed artifact is too large")
                for offset in range(0, len(chunk), 65536):
                    yield chunk[offset : offset + 65536]

        status, body = await self._request(
            "PUT",
            f"/v1/artifacts/{artifact_id}",
            params={"consumer_id": principal.consumer_id},
            headers={"X-Content-SHA256": sha256},
            content=bounded(),
        )
        try:
            return ArtifactPutResult(ArtifactWire.model_validate_json(body).domain(), status == 201)
        except ValidationError:
            raise dependency_error() from None

    async def retire(self, principal: Principal, artifact_id: UUID) -> None:
        await self._request(
            "DELETE", f"/v1/artifacts/{artifact_id}", params={"consumer_id": principal.consumer_id}
        )

    async def resolve_admission(self, spec: SandboxSpec) -> ProfileSnapshot:
        body = spec_wire(spec).model_dump(mode="json", exclude={"artifact_id"})
        _status, response = await self._request("POST", "/v1/profiles/resolve", json=body)
        try:
            return SnapshotWire.model_validate_json(response).domain()
        except ValidationError:
            raise dependency_error() from None

    def _control(
        self, principal: Principal, identity: OrchestratorIdentity, generation: int
    ) -> dict[str, Any]:
        return ControlRequest(
            consumer_id=principal.consumer_id,
            identity=IdentityWire(
                installation_id=identity.installation_id, host_id=identity.host_id
            ),
            generation=generation,
        ).model_dump(mode="json")

    @asynccontextmanager
    async def attach(
        self, principal: Principal, sandbox_id: UUID
    ) -> AsyncIterator[TerminalConnection]:
        url = f"{self._ws_url}/v1/sandboxes/{sandbox_id}/terminal?" + urlencode(
            {"consumer_id": principal.consumer_id}
        )
        try:
            async with NoRedirectConnect(
                url,
                additional_headers={"Authorization": self._authorization},
                open_timeout=self._timeout,
                close_timeout=2,
                ping_timeout=self._timeout,
                max_size=65536,
                max_queue=4,
                write_limit=65536,
                compression=None,
                proxy=None,
            ) as connection:
                yield OrchestratorTerminal(connection, self._timeout)
        except InvalidStatus as error:
            status = error.response.status_code
            if status in (404, 409):
                raise BoxieError(status, ERRORS[status]) from None
            raise dependency_error() from None
        except OSError, TimeoutError, InvalidHandshake:
            raise dependency_error() from None
