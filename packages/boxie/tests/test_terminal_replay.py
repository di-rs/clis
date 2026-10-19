"""Bounded PTY replay and current-attachment controls, independent of external services."""

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from boxie.domain.errors import BoxieError
from boxie.domain.models import SandboxState
from lifecycle_support import settled, setup


def test_ring_retention_sequence_and_atomic_live_cursor() -> None:
    assert importlib.util.find_spec("boxie.sandbox.terminal.replay"), "sequenced replay missing"
    from boxie.sandbox.terminal.replay import ReplayBuffer, StreamGap

    ring = ReplayBuffer(capacity=32768)
    ring.append(b"a" * 20000)
    assert [(f.sequence, len(f.data)) for f in ring.after(0)] == [(1, 16384), (2, 3616)]
    assert ring.after(1)[0].wire() == (2).to_bytes(8, "big") + b"a" * 3616
    ring.append(b"b" * 16384)
    assert ring.retained_bytes <= 32768
    with pytest.raises(StreamGap):
        ring.after(0)
    assert [f.sequence for f in ring.after(1)] == [2, 3]
    with pytest.raises(ValueError):
        ring.after(4)
    ring.append(b"\xf0\x9f")
    ring.append(b"\x99\x82")
    assert b"".join(f.data for f in ring.after(3)) == "🙂".encode()


def test_reattach_fences_stale_controls_and_terminal_replies_do_not_renew_idle(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        orchestrator, request, daemon, clock, _ = await setup(tmp_path)
        try:
            await settled(orchestrator, request.ref, SandboxState.READY)
            epoch = await orchestrator.attach(request.ref, attachment_order=1)
            newer = await orchestrator.attach(request.ref, attachment_order=2)
            assert newer == epoch + 1
            await orchestrator.detach(request.ref, epoch)
            assert orchestrator.records[request.ref.id].attached
            for operation in (
                orchestrator.write(request.ref, epoch, b"stale"),
                orchestrator.resize(request.ref, epoch, 81, 25),
            ):
                with pytest.raises(BoxieError):
                    await operation
            original = orchestrator.executions[request.ref.id].timers.idle
            clock.advance(20)
            await orchestrator.write(request.ref, newer, b"\x1b[1;1R", source="terminal")
            assert orchestrator.executions[request.ref.id].timers.idle == original
            assert orchestrator.records[request.ref.id].last_input_at is None
            await orchestrator.write(request.ref, newer, b"hello", source="user")
            assert orchestrator.executions[request.ref.id].timers.idle == clock.monotonic() + 600
        finally:
            await orchestrator.close()

    asyncio.run(exercise())


def test_pty_drain_does_not_wait_for_reader_and_payload_cap_is_exact(tmp_path: Path) -> None:
    async def exercise() -> None:
        orchestrator, request, daemon, _, _ = await setup(tmp_path)
        try:
            await settled(orchestrator, request.ref, SandboxState.READY)
            terminal = orchestrator.executions[request.ref.id].pty
            assert hasattr(terminal, "replay"), "PTY replay missing"
            stream = next(iter(daemon.streams.values()))
            # No terminal attachment or ACK: drain still reaches the hard cap.
            for _ in range(16):
                await stream.queue.put(b"x" * 1024 * 1024)
            await settled(orchestrator, request.ref, SandboxState.TERMINATED)
            assert orchestrator.records[request.ref.id].reason == "output_limit"
            assert terminal.remaining == 0
            assert terminal.replay.retained_bytes == 1024 * 1024
            assert terminal.replay.tail == 1024
        finally:
            await orchestrator.close()

    asyncio.run(exercise())


def test_network_replay_resize_barrier_and_browser_heartbeat(tmp_path: Path) -> None:
    import json
    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager

    from boxie.sandbox.transport.server import create_orchestrator_app
    from boxie_test_support.api import serve
    from boxie_test_support.artifacts import Artifacts
    from fastapi import FastAPI
    from websockets.asyncio.client import connect

    async def exercise() -> None:
        orchestrator, request, daemon, clock, _ = await setup(tmp_path)

        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            try:
                await orchestrator.start()
                yield
            finally:
                await orchestrator.close()

        async def readiness() -> dict[str, bool]:
            return {"docker": await daemon.ready(), "execution": orchestrator.execution_ready}

        app = create_orchestrator_app(
            orchestrator,
            Artifacts(tmp_path / "artifacts"),
            credential=b"secret",
            readiness=readiness,
            lifespan=lifespan,
        )
        async with serve(app) as origin:
            await settled(orchestrator, request.ref, SandboxState.READY)
            stream = next(iter(daemon.streams.values()))
            await stream.queue.put(b"first")
            await asyncio.sleep(0.01)
            url = (
                origin.replace("http:", "ws:")
                + f"/v1/sandboxes/{request.ref.id}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers={"Authorization": "Bearer secret"}) as old:
                await old.send(
                    json.dumps(
                        {
                            "type": "attach",
                            "attachment_order": 1,
                            "resume_after": 0,
                            "cols": 80,
                            "rows": 24,
                        }
                    )
                )
                attached = json.loads(await asyncio.wait_for(old.recv(), 1))
                assert attached["type"] == "attached", attached
                epoch = attached["epoch"]
                assert json.loads(await old.recv()) == {
                    "type": "geometry",
                    "epoch": epoch,
                    "sequence": 0,
                    "cols": 80,
                    "rows": 24,
                }
                assert await old.recv() == (1).to_bytes(8, "big") + b"first"
                assert json.loads(await old.recv()) == {
                    "type": "replay_complete",
                    "epoch": epoch,
                    "sequence": 1,
                }
                await old.send(
                    json.dumps(
                        {"type": "input", "epoch": epoch, "source": "terminal", "data": "reply"}
                    )
                )
                await old.send(
                    json.dumps({"type": "resize", "epoch": epoch, "cols": 100, "rows": 30})
                )
                assert await old.recv() == (2).to_bytes(8, "big") + b"reply"
                assert json.loads(await old.recv()) == {
                    "type": "resized",
                    "epoch": epoch,
                    "cols": 100,
                    "rows": 30,
                }
                assert orchestrator.records[request.ref.id].last_input_at is None
                async with connect(
                    url, additional_headers={"Authorization": "Bearer secret"}
                ) as new:
                    await new.send(
                        json.dumps(
                            {
                                "type": "attach",
                                "attachment_order": 2,
                                "resume_after": 2,
                                "cols": 100,
                                "rows": 30,
                            }
                        )
                    )
                    successor = json.loads(await new.recv())["epoch"]
                    assert successor > epoch
                    assert json.loads(await new.recv()) == {
                        "type": "geometry",
                        "epoch": successor,
                        "sequence": 2,
                        "cols": 100,
                        "rows": 30,
                    }
                    assert json.loads(await new.recv())["type"] == "replay_complete"
                    await old.close()
                    await new.send(
                        json.dumps({"type": "heartbeat", "epoch": successor, "nonce": 1})
                    )
                    assert json.loads(await new.recv()) == {
                        "type": "heartbeat",
                        "epoch": successor,
                        "nonce": 1,
                    }
                    assert orchestrator.records[request.ref.id].attached
                    # Relay-local websocket pings still work, but no browser heartbeat does.
                    await (await new.ping())
                    clock.advance(30)
                    await asyncio.wait_for(new.wait_closed(), 2)
                    assert not orchestrator.records[request.ref.id].attached
                    assert (
                        orchestrator.executions[request.ref.id].timers.disconnect
                        == clock.monotonic() + 120
                    )

    asyncio.run(exercise())


@pytest.mark.parametrize("late_resize", [False, True])
def test_terminal_end_during_replay_does_not_wait_for_resize(
    tmp_path: Path, late_resize: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from boxie_test_support.api import CONSUMER_AUTH, artifact, create, ready, terminal_url
    from boxie_test_support.terminal_network import network
    from websockets.asyncio.client import connect

    async def exercise() -> None:
        async with network(tmp_path) as net:
            identifier = await create(net.api, await artifact(net.api))
            await ready(net.api, identifier)
            async with connect(
                terminal_url(net.api, identifier), additional_headers=CONSUMER_AUTH
            ) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "attach",
                            "attachment_order": 1,
                            "resume_after": 0,
                            "cols": 80,
                            "rows": 24,
                        }
                    )
                )
                assert json.loads(await ws.recv())["type"] == "attached"
                assert json.loads(await ws.recv())["type"] == "geometry"
                assert isinstance(await ws.recv(), bytes)
                assert json.loads(await ws.recv())["type"] == "replay_complete"
                if late_resize:
                    from fastapi import WebSocket

                    original = WebSocket.send_json

                    async def delayed_status(
                        self: WebSocket, data: Any, mode: str = "text"
                    ) -> None:
                        if data.get("type") == "status":
                            await asyncio.sleep(0.05)
                        await original(self, data, mode)

                    monkeypatch.setattr(WebSocket, "send_json", delayed_status)
                await net.api.delete(f"/v1/sandboxes/{identifier}")
                if late_resize:
                    await ws.send(
                        json.dumps({"type": "resize", "epoch": 1, "cols": 100, "rows": 30})
                    )
                assert json.loads(await asyncio.wait_for(ws.recv(), 0.5))["reason"] == "requested"

    asyncio.run(exercise())


def test_replay_retains_geometry_without_unbounded_resize_journal() -> None:
    from boxie.sandbox.terminal.replay import ReplayBuffer, StreamGap

    ring = ReplayBuffer()
    assert hasattr(ring, "geometry"), "replay geometry missing"
    ring.append(b"old")
    ring.resize(100, 30)
    ring.append(b"new")
    assert [(f.data, f.geometry) for f in ring.after(0)] == [
        (b"old", (80, 24)),
        (b"new", (100, 30)),
    ]
    for index in range(10000):
        ring.resize(80 + index % 20, 24)
    assert len(ring.frames) == 2 and ring.geometry == (99, 24)
    for _ in range(4096):
        ring.append(b"x")
    assert len(ring.frames) == 4096
    with pytest.raises(StreamGap):
        ring.after(0)
    assert ring.retained_bytes == 4096


@pytest.mark.parametrize("failure", ["blocked", "failed"])
def test_failed_resize_releases_output_order_without_recording_unapplied_geometry(
    tmp_path: Path, failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        orchestrator, request, daemon, _, _ = await setup(tmp_path, docker_timeout=0.02)
        try:
            await settled(orchestrator, request.ref, SandboxState.READY)
            epoch = await orchestrator.attach(request.ref, attachment_order=1)
            stream = next(iter(daemon.streams.values()))

            async def resize(cols: int, rows: int) -> None:
                if failure == "failed":
                    raise OSError("resize failed")
                await asyncio.Event().wait()

            monkeypatch.setattr(stream, "resize", resize)
            with pytest.raises((TimeoutError, OSError)):
                await orchestrator.resize(request.ref, epoch, 100, 30)
            stream.queue.put_nowait(b"after")
            terminal = orchestrator.executions[request.ref.id].pty
            await asyncio.wait_for(terminal.replay.changed.wait(), 0.2)
            assert terminal.replay.after(0)[0].geometry == (80, 24)
            assert await orchestrator.attach(request.ref, attachment_order=2) == epoch + 1
        finally:
            await orchestrator.close()

    asyncio.run(exercise())


def test_attachment_order_persists_and_preserves_cursor_and_journal_failure_fences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from boxie.sandbox.execution.journal import ExecutionJournal

    async def exercise() -> None:
        orchestrator, request, _, _, _ = await setup(tmp_path)
        original_save = orchestrator.journal.save
        try:
            await settled(orchestrator, request.ref, SandboxState.READY)
            epoch = await orchestrator.attach(request.ref, attachment_order=3, resume_after=0)
            saved = ExecutionJournal(
                orchestrator.journal.path, orchestrator.config.identity
            ).records[request.ref.id]
            assert (
                saved.attachment_order == 3 and saved.attachment_epoch == epoch and saved.attached
            )
            snapshot = orchestrator.records[request.ref.id].model_dump()
            deadlines = vars(orchestrator.executions[request.ref.id].timers).copy()
            for order in (1, 3):
                with pytest.raises(BoxieError):
                    await orchestrator.attach(request.ref, attachment_order=order, resume_after=0)
            with pytest.raises(ValueError):
                await orchestrator.attach(request.ref, attachment_order=4, resume_after=999)
            assert orchestrator.records[request.ref.id].model_dump() == snapshot
            assert vars(orchestrator.executions[request.ref.id].timers) == deadlines
            newer = await orchestrator.attach(request.ref, attachment_order=4, resume_after=0)
            await orchestrator.detach(request.ref, epoch)
            assert orchestrator.records[request.ref.id].attached
            assert newer == epoch + 1

            def failed_save() -> None:
                raise OSError("journal unavailable")

            monkeypatch.setattr(orchestrator.journal, "save", failed_save)
            with pytest.raises(OSError):
                await orchestrator.attach(request.ref, attachment_order=5, resume_after=0)
            assert not orchestrator.execution_ready
            with pytest.raises(BoxieError):
                await orchestrator.attach(request.ref, attachment_order=6, resume_after=0)
            saved = ExecutionJournal(
                orchestrator.journal.path, orchestrator.config.identity
            ).records[request.ref.id]
            assert saved.attachment_order == 4 and saved.attachment_epoch == newer
            monkeypatch.setattr(orchestrator.journal, "save", original_save)
            # Same full-journal recovery boundary as the supervised sweeper.
            orchestrator._save()
            saved = ExecutionJournal(
                orchestrator.journal.path, orchestrator.config.identity
            ).records[request.ref.id]
            assert saved.attachment_order == 5
            with pytest.raises(BoxieError):
                await orchestrator.attach(request.ref, attachment_order=5, resume_after=0)
            await orchestrator.attach(request.ref, attachment_order=6, resume_after=0)
        finally:
            monkeypatch.setattr(orchestrator.journal, "save", original_save)
            await orchestrator.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("boundary", ["api", "orchestrator"])
@pytest.mark.parametrize("healthy", [False, True])
def test_attachment_order_requires_json_integer_without_changing_epoch_or_grace(
    tmp_path: Path, boundary: str, healthy: bool
) -> None:
    import json
    from contextlib import AsyncExitStack
    from uuid import UUID

    from boxie_test_support.api import CONSUMER_AUTH, artifact, create, ready, terminal_url
    from boxie_test_support.terminal_network import attach, network
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    async def exercise() -> None:
        async with network(tmp_path) as net, AsyncExitStack() as connections:
            identifier = await create(net.api, await artifact(net.api))
            await ready(net.api, identifier)
            url = terminal_url(net.api, identifier)
            headers = CONSUMER_AUTH
            if boundary == "orchestrator":
                url = (
                    net.orchestrator_url.replace("http:", "ws:")
                    + f"/v1/sandboxes/{identifier}/terminal?consumer_id=one"
                )
                headers = {"Authorization": "Bearer orchestrator-test-secret"}
            if healthy:
                current = await connections.enter_async_context(
                    connect(url, additional_headers=headers)
                )
                await attach(current, order=1)
            net.clock.advance(10)
            record = net.orchestrator.records[UUID(identifier)]
            before = record.model_dump()
            grace = vars(net.orchestrator.executions[record.id].timers).copy()
            missing = object()
            for invalid in (True, False, "2", 3.0, None, missing, 0, -1, 2**53):
                request: dict[str, object] = {
                    "type": "attach",
                    "resume_after": 0,
                    "cols": 80,
                    "rows": 24,
                }
                if invalid is not missing:
                    request["attachment_order"] = invalid
                async with connect(url, additional_headers=headers) as rejected:
                    await rejected.send(json.dumps(request))
                    with pytest.raises(ConnectionClosed):
                        await asyncio.wait_for(rejected.recv(), 1)
                assert record.model_dump() == before
                assert vars(net.orchestrator.executions[record.id].timers) == grace
            if healthy:
                assert current.close_code is None
                await current.send(
                    json.dumps(
                        {
                            "type": "input",
                            "epoch": record.attachment_epoch,
                            "source": "user",
                            "data": "healthy",
                        }
                    )
                )
                assert (
                    await asyncio.wait_for(current.recv(), 1) == (2).to_bytes(8, "big") + b"healthy"
                )
            async with connect(url, additional_headers=headers) as maximum:
                await attach(maximum, order=2**53 - 1)
                assert record.attachment_order == 2**53 - 1 and record.attached
                assert record.attachment_epoch == before["attachment_epoch"] + 1

    asyncio.run(exercise())
