"""Translate bounded consumer JSON input into the byte-preserving agent stream."""

import asyncio
from contextlib import suppress
from uuid import UUID

from fastapi import APIRouter, WebSocket
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from boxie.api.auth import ConsumerAuth
from boxie.application.service import Service, TerminalConnection
from boxie.domain.errors import BoxieError, TerminalWriteClosed
from boxie.sandbox.terminal_protocol import (
    Ack,
    Attached,
    AttachRequest,
    Heartbeat,
    Input,
    Resize,
    control,
    response,
)

DRAIN_TIMEOUT = 2.0


async def close_socket(websocket: WebSocket, code: int = 1000) -> None:
    with suppress(WebSocketDisconnect, RuntimeError, TimeoutError, OSError):
        async with asyncio.timeout(2):
            await websocket.close(code=code)


async def proxy(websocket: WebSocket, connection: TerminalConnection) -> None:
    epoch: int | None = None
    cursor = acked = 0
    last_nonce = -1

    async def receive() -> None:
        nonlocal cursor, acked, last_nonce
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is None or len(text.encode()) > 16384:
                await close_socket(websocket, 1008)
                return
            try:
                frame = control.validate_json(text)
                if isinstance(frame, AttachRequest):
                    cursor = acked = frame.resume_after
                try:
                    await connection.send(frame.model_dump_json())
                except TerminalWriteClosed:
                    if not (
                        isinstance(frame, (Ack, Heartbeat, Input, Resize))
                        and frame.epoch == epoch
                        and (not isinstance(frame, Ack) or acked <= frame.sequence <= cursor)
                        and (not isinstance(frame, Heartbeat) or frame.nonce > last_nonce)
                    ):
                        raise
                    # A valid late control may race a normal upstream close.
                    # Only a delivered terminal status can complete this drain.
                    async with asyncio.timeout(DRAIN_TIMEOUT):
                        await asyncio.shield(output_task)
                    return
                if isinstance(frame, Ack):
                    acked = frame.sequence
                elif isinstance(frame, Heartbeat):
                    last_nonce = frame.nonce
            except ValueError, ValidationError, UnicodeError:
                await close_socket(websocket, 1008)
                return

    async def send() -> None:
        nonlocal epoch, cursor
        while True:
            message = await connection.receive()
            async with asyncio.timeout(20):
                if isinstance(message, bytes):
                    if not 9 <= len(message) <= 16392:
                        raise BoxieError(503, "invalid agent output frame")
                    cursor = int.from_bytes(message[:8], "big")
                    await websocket.send_bytes(message)
                else:
                    try:
                        status = response.validate_json(message)
                    except ValidationError:
                        raise BoxieError(503, "invalid agent terminal status") from None
                    if isinstance(status, Attached):
                        epoch = status.epoch
                    await websocket.send_text(status.model_dump_json())
                    if status.type == "status":
                        await close_socket(websocket)
                        return

    output_task = asyncio.create_task(send())
    tasks = [asyncio.create_task(receive()), output_task]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
        async with asyncio.timeout(2):
            results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException) and not isinstance(
                result, (asyncio.CancelledError, WebSocketDisconnect, BoxieError)
            ):
                raise result


def terminal_routes(service: Service, auth: ConsumerAuth) -> APIRouter:
    router = APIRouter()

    @router.websocket("/v1/sandboxes/{sandbox_id}/terminal")
    async def terminal(websocket: WebSocket, sandbox_id: UUID) -> None:
        try:
            principal = auth.resolve(websocket.headers.get("Authorization", ""))
            await service.lifecycle.get(principal, sandbox_id)
            async with service.terminal.attach(principal, sandbox_id) as connection:
                async with asyncio.timeout(10):
                    await websocket.accept()
                await proxy(websocket, connection)
        except BoxieError as error:
            if websocket.application_state == WebSocketState.CONNECTING:
                await websocket.send_denial_response(
                    JSONResponse({"detail": str(error)}, status_code=error.status)
                )
            elif websocket.application_state == WebSocketState.CONNECTED:
                await close_socket(websocket, 1011)
        except TimeoutError, WebSocketDisconnect:
            if websocket.application_state == WebSocketState.CONNECTED:
                await close_socket(websocket, 1011)

    return router
