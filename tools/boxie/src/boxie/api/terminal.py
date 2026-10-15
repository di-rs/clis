"""Translate bounded consumer JSON input into the byte-preserving agent stream."""

import asyncio
import json
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, WebSocket
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError
from starlette.websockets import WebSocketDisconnect, WebSocketState

from boxie.api.auth import ConsumerAuth
from boxie.application.service import Service, TerminalConnection
from boxie.domain.errors import BoxieError
from boxie.sandbox.protocol import ResizeWire, StatusWire, WireModel


class InputWire(WireModel):
    type: Literal["input"]
    data: Annotated[str, Field(min_length=1)]


async def proxy(websocket: WebSocket, connection: TerminalConnection) -> None:
    async def receive() -> None:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            text = message.get("text")
            if text is None or len(text.encode()) > 16384:
                await websocket.close(code=1008)
                return
            try:
                data = json.loads(text)
                if isinstance(data, dict) and data.get("type") == "input":
                    frame = InputWire.model_validate(data)
                    await connection.send(frame.data.encode("utf-8"))
                else:
                    size = ResizeWire.model_validate(data)
                    await connection.send(size.model_dump_json())
            except ValueError, ValidationError, UnicodeError:
                await websocket.close(code=1008)
                return

    async def send() -> None:
        while True:
            message = await connection.receive()
            async with asyncio.timeout(20):
                if isinstance(message, bytes):
                    # Keep output bounded even if the upstream daemon returns a large read.
                    for offset in range(0, len(message), 65536):
                        await websocket.send_bytes(message[offset : offset + 65536])
                else:
                    try:
                        status = StatusWire.model_validate_json(message)
                    except ValidationError:
                        raise BoxieError(503, "invalid agent terminal status") from None
                    await websocket.send_json(status.model_dump(mode="json"))
                    await websocket.close()
                    return

    tasks = [asyncio.create_task(receive()), asyncio.create_task(send())]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    except WebSocketDisconnect:
        pass
    finally:
        for task in tasks:
            task.cancel()
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
                await websocket.accept()
                await proxy(websocket, connection)
        except BoxieError as error:
            if websocket.application_state == WebSocketState.CONNECTING:
                await websocket.send_denial_response(
                    JSONResponse({"detail": str(error)}, status_code=error.status)
                )
            elif websocket.application_state == WebSocketState.CONNECTED:
                await websocket.close(code=1011)
        except TimeoutError, WebSocketDisconnect:
            if websocket.application_state == WebSocketState.CONNECTED:
                await websocket.close(code=1011)

    return router
