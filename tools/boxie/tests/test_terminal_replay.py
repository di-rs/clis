"""Bounded PTY replay and current-attachment controls, independent of external services."""

import asyncio
import importlib.util
from pathlib import Path
from typing import Any

import pytest
from boxie.domain.errors import BoxieError
from boxie.domain.models import SandboxState
from test_agent_lifecycle import settled, setup


def test_ring_retention_sequence_and_atomic_live_cursor() -> None:
    assert importlib.util.find_spec("boxie.sandbox.replay"), "sequenced replay missing"
    from boxie.sandbox.replay import ReplayBuffer, StreamGap

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
        agent, request, daemon, clock, _ = await setup(tmp_path)
        try:
            await settled(agent, request.ref, SandboxState.READY)
            epoch = await agent.attach(request.ref)
            newer = await agent.attach(request.ref)
            assert newer == epoch + 1
            await agent.detach(request.ref, epoch)
            assert agent.records[request.ref.id].attached
            for operation in (
                agent.write(request.ref, epoch, b"stale"),
                agent.resize(request.ref, epoch, 81, 25),
            ):
                with pytest.raises(BoxieError):
                    await operation
            original = agent.deadlines[request.ref.id].idle
            clock.advance(20)
            await agent.write(request.ref, newer, b"\x1b[1;1R", source="terminal")
            assert agent.deadlines[request.ref.id].idle == original
            assert agent.records[request.ref.id].last_input_at is None
            await agent.write(request.ref, newer, b"hello", source="user")
            assert agent.deadlines[request.ref.id].idle == clock.monotonic() + 600
        finally:
            await agent.close()

    asyncio.run(exercise())


def test_pty_drain_does_not_wait_for_reader_and_payload_cap_is_exact(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, _, _ = await setup(tmp_path)
        try:
            await settled(agent, request.ref, SandboxState.READY)
            terminal = agent.terminals[request.ref.id]
            assert hasattr(terminal, "replay"), "PTY replay missing"
            stream = next(iter(daemon.streams.values()))
            # No terminal attachment or ACK: drain still reaches the hard cap.
            for _ in range(16):
                await stream.queue.put(b"x" * 1024 * 1024)
            await settled(agent, request.ref, SandboxState.TERMINATED)
            assert agent.records[request.ref.id].reason.value == "output_limit"
            assert terminal.remaining == 0
            assert terminal.replay.retained_bytes == 1024 * 1024
            assert terminal.replay.tail == 1024
        finally:
            await agent.close()

    asyncio.run(exercise())


def test_network_replay_resize_barrier_and_browser_heartbeat(tmp_path: Path) -> None:
    import json
    from types import SimpleNamespace

    from boxie.sandbox.durable_server import create_durable_agent_app
    from boxie.sandbox.reporting import ObservationReporter
    from boxie_test_support.api import serve
    from websockets.asyncio.client import connect

    async def exercise() -> None:
        agent, request, daemon, clock, _ = await setup(tmp_path)

        async def noop() -> None:
            pass

        artifacts: Any = SimpleNamespace(collect=noop, collect_orphans=noop)
        reporter = ObservationReporter("http://127.0.0.1:1", b"unused")
        app = create_durable_agent_app(agent, artifacts, credential=b"secret", reporter=reporter)
        async with serve(app) as origin:
            await settled(agent, request.ref, SandboxState.READY)
            stream = next(iter(daemon.streams.values()))
            await stream.queue.put(b"first")
            await asyncio.sleep(0.01)
            url = (
                origin.replace("http:", "ws:")
                + f"/v1/sandboxes/{request.ref.id}/terminal?consumer_id=one"
            )
            async with connect(url, additional_headers={"Authorization": "Bearer secret"}) as old:
                await old.send(
                    json.dumps({"type": "attach", "resume_after": 0, "cols": 80, "rows": 24})
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
                assert agent.records[request.ref.id].last_input_at is None
                async with connect(
                    url, additional_headers={"Authorization": "Bearer secret"}
                ) as new:
                    await new.send(
                        json.dumps({"type": "attach", "resume_after": 2, "cols": 100, "rows": 30})
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
                    assert agent.records[request.ref.id].attached
                    # Relay-local websocket pings still work, but no browser heartbeat does.
                    await (await new.ping())
                    clock.advance(30)
                    await asyncio.wait_for(new.wait_closed(), 2)
                    assert not agent.records[request.ref.id].attached
                    assert agent.deadlines[request.ref.id].disconnect == clock.monotonic() + 120

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
                    json.dumps({"type": "attach", "resume_after": 0, "cols": 80, "rows": 24})
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
    from boxie.sandbox.replay import ReplayBuffer, StreamGap

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
        agent, request, daemon, _, _ = await setup(tmp_path, docker_timeout=0.02)
        try:
            await settled(agent, request.ref, SandboxState.READY)
            epoch = await agent.attach(request.ref)
            stream = next(iter(daemon.streams.values()))

            async def resize(cols: int, rows: int) -> None:
                if failure == "failed":
                    raise OSError("resize failed")
                await asyncio.Event().wait()

            monkeypatch.setattr(stream, "resize", resize)
            with pytest.raises((TimeoutError, OSError)):
                await agent.resize(request.ref, epoch, 100, 30)
            stream.queue.put_nowait(b"after")
            terminal = agent.terminals[request.ref.id]
            await asyncio.wait_for(terminal.replay.changed.wait(), 0.2)
            assert terminal.replay.after(0)[0].geometry == (80, 24)
            assert await agent.attach(request.ref) == epoch + 1
        finally:
            await agent.close()

    asyncio.run(exercise())
