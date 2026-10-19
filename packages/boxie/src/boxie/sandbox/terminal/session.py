"""One epoch-owned browser path, with bounded delivery and a resize barrier."""

import asyncio
from collections import deque
from contextlib import suppress

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from boxie.contracts.terminal import (
    Ack,
    AttachRequest,
    Detach,
    Heartbeat,
    Input,
    Resize,
    control,
)
from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import SandboxRef
from boxie.domain.models import SandboxState
from boxie.sandbox.execution.orchestrator import Orchestrator
from boxie.sandbox.execution.sandbox import TerminalSession
from boxie.sandbox.terminal.replay import StreamGap

SEND_TIMEOUT = 10.0
CLOSE_TIMEOUT = 2.0
HEARTBEAT_TIMEOUT = 30.0
WINDOW_BYTES = 64 * 1024


async def close_socket(websocket: WebSocket, code: int = 1000) -> None:
    with suppress(WebSocketDisconnect, RuntimeError, TimeoutError, OSError):
        async with asyncio.timeout(CLOSE_TIMEOUT):
            await websocket.close(code=code)


async def serve_terminal(websocket: WebSocket, orchestrator: Orchestrator, ref: SandboxRef) -> None:
    with orchestrator.terminal_session(ref) as session:
        await _serve_terminal(websocket, orchestrator, ref, session)


async def _serve_terminal(
    websocket: WebSocket,
    orchestrator: Orchestrator,
    ref: SandboxRef,
    session: TerminalSession,
) -> None:
    terminal = session.terminal
    ring = terminal.replay
    epoch: int | None = None
    tasks: list[asyncio.Task[None]] = []
    sending = asyncio.Lock()

    async def send(value: bytes | dict[str, object]) -> None:
        async with asyncio.timeout(SEND_TIMEOUT):
            async with sending:
                if isinstance(value, bytes):
                    await websocket.send_bytes(value)
                else:
                    await websocket.send_json(value)

    try:
        async with asyncio.timeout(10):
            await websocket.accept()
        # Opening either private relay alone is not a successful browser attachment.
        async with asyncio.timeout(10):
            text = await websocket.receive_text()
        if len(text.encode()) > 1024:
            raise ValueError("attach too large")
        request = AttachRequest.model_validate_json(text)
        ring.after(request.resume_after)
        epoch = await orchestrator.attach(
            ref, attachment_order=request.attachment_order, resume_after=request.resume_after
        )
        # No await between validation/cursor snapshot and registering delivery.
        cursor = request.resume_after
        boundary = ring.tail
        boundary_geometry = ring.geometry
        acked = cursor
        outstanding: deque[tuple[int, int]] = deque()
        pending_bytes = 0
        last_heartbeat = orchestrator.monotonic()
        last_nonce = -1
        resize: Resize | None = None
        replaying = True
        input_ready = False
        await send({"type": "attached", "epoch": epoch})

        async def receive() -> None:
            nonlocal acked, pending_bytes, last_heartbeat, last_nonce, resize, input_ready
            assert epoch is not None
            while True:
                text = await websocket.receive_text()
                if len(text.encode()) > 16384:
                    raise ValueError("control too large")
                message = control.validate_json(text)
                if isinstance(message, AttachRequest) or message.epoch != epoch:
                    raise ValueError("incorrect attachment epoch")
                await orchestrator.check_attachment(ref, epoch, allow_ended=True)
                # Late input/resize must not cancel final output/status delivery.
                # Only ACKs and heartbeat still have work while the terminal drains.
                if session.state != SandboxState.READY and not isinstance(
                    message, (Ack, Heartbeat)
                ):
                    continue
                if isinstance(message, Input):
                    if message.source == "user" and not input_ready:
                        raise ValueError("input before resize barrier")
                    try:
                        await orchestrator.write(
                            ref, epoch, message.data.encode(), source=message.source
                        )
                    except BoxieError:
                        if session.reason is None:
                            raise
                elif isinstance(message, Ack):
                    if not acked <= message.sequence <= cursor:
                        raise ValueError("invalid acknowledgement")
                    acked = message.sequence
                    while outstanding and outstanding[0][0] <= acked:
                        pending_bytes -= outstanding.popleft()[1]
                elif isinstance(message, Resize):
                    if resize is not None:
                        raise ValueError("resize already pending")
                    input_ready = False
                    resize = message
                elif isinstance(message, Heartbeat):
                    if message.nonce <= last_nonce:
                        raise ValueError("stale heartbeat")
                    last_nonce = message.nonce
                    last_heartbeat = orchestrator.monotonic()
                    await send(message.model_dump())
                elif isinstance(message, Detach):
                    return
                ring.changed.set()

        async def output() -> None:
            nonlocal cursor, boundary, pending_bytes, replaying, resize, input_ready
            assert epoch is not None
            sent_geometry: tuple[int, int] | None = None
            while True:
                await orchestrator.check_attachment(ref, epoch, allow_ended=True)
                ring.changed.clear()
                frames = ring.after(cursor)
                limit = boundary if replaying else ring.tail
                blocked = False
                for frame in frames:
                    if frame.sequence > limit:
                        break
                    if pending_bytes + len(frame.data) > WINDOW_BYTES:
                        blocked = True
                        break
                    if frame.geometry != sent_geometry:
                        await send(
                            {
                                "type": "geometry",
                                "epoch": epoch,
                                "sequence": cursor,
                                "cols": frame.geometry[0],
                                "rows": frame.geometry[1],
                            }
                        )
                        sent_geometry = frame.geometry
                    cursor = frame.sequence
                    outstanding.append((cursor, len(frame.data)))
                    pending_bytes += len(frame.data)
                    await send(frame.wire())
                if replaying and cursor == boundary:
                    if boundary_geometry != sent_geometry:
                        await send(
                            {
                                "type": "geometry",
                                "epoch": epoch,
                                "sequence": cursor,
                                "cols": boundary_geometry[0],
                                "rows": boundary_geometry[1],
                            }
                        )
                        sent_geometry = boundary_geometry
                    await send({"type": "replay_complete", "epoch": epoch, "sequence": boundary})
                    replaying = False
                    # Wait for the browser's write-drain before beginning catch-up.
                    while resize is None and not ring.closed:
                        await ring.changed.wait()
                        ring.changed.clear()
                    if ring.closed and cursor < ring.tail:
                        continue
                if resize is not None and not blocked:
                    # Capture catch-up before resizing. New output is sent only after
                    # resized, so the browser's ordered write barrier owns local fit.
                    if cursor < ring.tail:
                        continue
                    size, resize = resize, None
                    try:
                        await orchestrator.resize(ref, epoch, size.cols, size.rows)
                    except BoxieError:
                        if session.reason is None:
                            raise
                        continue
                    await send(
                        {"type": "resized", "epoch": epoch, "cols": size.cols, "rows": size.rows}
                    )
                    sent_geometry = (size.cols, size.rows)
                    input_ready = True
                if ring.closed and cursor == ring.tail:
                    reason = session.reason
                    await send({"type": "status", "reason": reason.value if reason else "exited"})
                    return
                await ring.changed.wait()

        async def heartbeat() -> None:
            assert epoch is not None
            while True:
                await asyncio.sleep(0.25)
                await orchestrator.check_attachment(ref, epoch, allow_ended=True)
                if orchestrator.monotonic() - last_heartbeat >= HEARTBEAT_TIMEOUT:
                    return

        tasks = [
            asyncio.create_task(output()),
            asyncio.create_task(receive()),
            asyncio.create_task(heartbeat()),
        ]
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    except StreamGap:
        reason = session.reason
        await send({"type": "status", "reason": reason.value if reason else "stream_gap"})
    except WebSocketDisconnect, BoxieError, ValueError, TimeoutError, RuntimeError, OSError:
        pass
    finally:
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                async with asyncio.timeout(CLOSE_TIMEOUT):
                    await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            try:
                if epoch is not None:
                    await orchestrator.detach(ref, epoch)
            finally:
                await close_socket(websocket)
