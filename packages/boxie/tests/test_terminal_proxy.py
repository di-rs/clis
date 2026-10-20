"""Production API and supervisor protocol over independent loopback sockets."""

import asyncio
import json
from pathlib import Path

import pytest
from boxie_test_support.api import (
    CONSUMER_AUTH,
    FOREIGN_AUTH,
    artifact,
    create,
    ready,
    terminal_url,
)
from boxie_test_support.terminal_network import attach, network
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


def test_terminal_authentication_replay_and_unicode_through_api(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with network(tmp_path) as net:
            identifier = await create(net.api, await artifact(net.api))
            await ready(net.api, identifier)
            url = terminal_url(net.api, identifier)
            for headers, status in (({}, 401), (FOREIGN_AUTH, 404)):
                with pytest.raises(InvalidStatus) as error:
                    async with connect(url, additional_headers=headers):
                        pytest.fail("unauthorized terminal opened")
                assert error.value.response.status_code == status
            async with connect(url, additional_headers=CONSUMER_AUTH) as old:
                epoch, payload = await attach(old, order=1)
                assert payload == b"ready\n"
                await old.send(
                    json.dumps({"type": "input", "epoch": epoch, "source": "user", "data": "é漢🙂"})
                )
                assert await old.recv() == (2).to_bytes(8, "big") + "é漢🙂".encode()
                async with connect(url, additional_headers=CONSUMER_AUTH) as new:
                    successor, replay = await attach(new, cursor=1, cols=100, rows=30, order=2)
                    assert replay == "é漢🙂".encode() and successor > epoch
                    await old.close()
                    await new.send(
                        json.dumps(
                            {"type": "input", "epoch": successor, "source": "user", "data": "once"}
                        )
                    )
                    assert await new.recv() == (3).to_bytes(8, "big") + b"once"
                    assert next(iter(net.daemon.streams.values())).sizes[-1] == (100, 30)
                    await net.api.delete(f"/v1/sandboxes/{identifier}")
                    assert json.loads(await new.recv()) == {"type": "status", "reason": "requested"}

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "frame",
    [
        b"raw",
        '{"type":"input","data":"' + "é" * 8192 + '"}',
        '{"type":"resize","epoch":1,"cols":501,"rows":24}',
        '{"type":"input","epoch":1,"source":"user","data":"ok","extra":true}',
        '{"cols":80,"rows":24}',
        '{"type":"input","epoch":1,"source":"user","data":"\\ud800"}',
    ],
)
def test_malformed_or_oversized_terminal_control_closes(tmp_path: Path, frame: str | bytes) -> None:
    async def exercise() -> None:
        async with network(tmp_path) as net:
            identifier = await create(net.api, await artifact(net.api))
            await ready(net.api, identifier)
            async with connect(
                terminal_url(net.api, identifier), additional_headers=CONSUMER_AUTH
            ) as ws:
                await attach(ws, order=1)
                await ws.send(frame)
                with pytest.raises(ConnectionClosed):
                    await asyncio.wait_for(ws.recv(), 1)

    asyncio.run(exercise())


def test_large_output_frame_boundaries_preserve_split_unicode_and_ack_window(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        async with network(tmp_path) as net:
            identifier = await create(net.api, await artifact(net.api))
            await ready(net.api, identifier)
            async with connect(
                terminal_url(net.api, identifier), additional_headers=CONSUMER_AUTH
            ) as ws:
                epoch, _ = await attach(ws, order=1)
                data = b"x" * 65535 + "🙂".encode() + b"end"
                next(iter(net.daemon.streams.values())).queue.put_nowait(data)
                received = bytearray()
                for sequence in range(2, 7):
                    frame = await asyncio.wait_for(ws.recv(), 1)
                    assert isinstance(frame, bytes) and len(frame) <= 16392
                    assert int.from_bytes(frame[:8], "big") == sequence
                    received.extend(frame[8:])
                    await ws.send(json.dumps({"type": "ack", "epoch": epoch, "sequence": sequence}))
                assert received == data

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "phase,kind,close_code",
    [
        ("before", "ack", 1000),
        ("after", "ack", 1011),
        ("during", "ack", 1000),
        ("after", "heartbeat", 1000),
        ("during", "input", 1000),
        ("after", "resize", 1000),
    ],
)
def test_final_control_preserves_buffered_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str, kind: str, close_code: int
) -> None:
    from urllib.parse import urlsplit

    from boxie.sandbox.transport.terminal import SupervisorTerminal
    from boxie_test_support.terminal_completion import CompletionBarrier

    async def exercise() -> None:
        barrier = CompletionBarrier(monkeypatch, SupervisorTerminal, phase, kind, close_code)
        async with network(tmp_path) as net:
            profiles = json.loads(net.config.profiles_file.read_text())
            profiles["default"]["limits"] = {"output_bytes": 10}
            net.config.profiles_file.write_text(json.dumps(profiles))
            net.supervisor.profiles.reload()
            barrier.port = urlsplit(str(net.api.base_url)).port
            identifier = await create(net.api, await artifact(net.api))
            await ready(net.api, identifier)
            async with connect(
                terminal_url(net.api, identifier), additional_headers=CONSUMER_AUTH
            ) as ws:
                epoch, initial = await attach(ws, order=1)
                assert initial == b"ready\n"
                next(iter(net.daemon.streams.values())).queue.put_nowait(b"last")
                assert await asyncio.wait_for(ws.recv(), 1) == (2).to_bytes(8, "big") + b"last"
                if close_code != 1000:
                    with pytest.raises(ConnectionClosed):
                        await barrier.finish(ws, epoch)
                    assert ws.close_code == 1011
                    return
                assert json.loads(await barrier.finish(ws, epoch)) == {
                    "type": "status",
                    "reason": "output_limit",
                }
                await asyncio.wait_for(ws.wait_closed(), 1)
                assert ws.close_code == 1000

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "scenario",
    [
        "stale-epoch",
        "future-ack",
        "backward-ack",
        "stale-heartbeat",
        "stale-input",
        "stale-resize",
        "flood-stalled",
        "detach",
        "malformed",
        "disconnect",
        "transport-error",
        "send-timeout",
        "missing-status",
        "stalled-status",
    ],
)
def test_closed_control_drain_keeps_abort_and_error_boundaries(
    monkeypatch: pytest.MonkeyPatch, scenario: str
) -> None:
    from boxie.api import terminal
    from boxie.contracts.terminal import response
    from boxie.domain.errors import BoxieError, TerminalWriteClosed
    from boxie_test_support.terminal_completion import failed_drain

    async def exercise() -> None:
        monkeypatch.setattr(terminal, "DRAIN_TIMEOUT", 0.02)
        result = await failed_drain(
            terminal.proxy,
            TerminalWriteClosed(),
            BoxieError(503, "transport failed"),
            scenario,
            BoxieError(503, "missing status"),
            decode_event=response.validate_json,
        )
        if scenario in {"malformed", "disconnect"}:
            assert result is None
        elif scenario in {"send-timeout", "stalled-status", "flood-stalled"}:
            assert isinstance(result, TimeoutError)
        else:
            assert isinstance(result, BoxieError)

    asyncio.run(exercise())
