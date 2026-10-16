"""Bounded authenticated private-orchestrator network adapter; no Docker or disk access."""

import asyncio

from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from boxie.domain.errors import BoxieError
from boxie.sandbox.protocol import (
    StatusWire,
)


def dependency_error() -> BoxieError:
    return BoxieError(503, "orchestrator unavailable")


class NoRedirectConnect(connect):
    def process_redirect(self, exc: Exception) -> Exception:
        return exc


class OrchestratorTerminal:
    def __init__(self, connection: ClientConnection, timeout: float) -> None:
        self._connection, self._timeout = connection, timeout

    async def receive(self) -> bytes | str:
        try:
            message = await self._connection.recv()
            if isinstance(message, str):
                return StatusWire.model_validate_json(message).model_dump_json()
            return message
        except ConnectionClosed:
            raise dependency_error() from None
        except ValidationError:
            raise dependency_error() from None

    async def send(self, data: bytes | str) -> None:
        try:
            async with asyncio.timeout(self._timeout):
                await self._connection.send(data)
        except ConnectionClosed, TimeoutError:
            raise dependency_error() from None
