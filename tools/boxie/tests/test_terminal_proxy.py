import asyncio
import json
from pathlib import Path

import pytest
from api_test_support import (
    CONSUMER_AUTH,
    FOREIGN_AUTH,
    artifact,
    create,
    ready,
    stack,
    terminal_url,
)
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus


def test_terminal_json_input_binary_output_resize_and_stop(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with stack(tmp_path) as (client, _agent, daemon, _clock):
            identifier = await create(client, await artifact(client))
            await ready(client, identifier)
            url = terminal_url(client, identifier)
            for headers, status in (({}, 401), (FOREIGN_AUTH, 404)):
                with pytest.raises(InvalidStatus) as error:
                    async with connect(url, additional_headers=headers):
                        pytest.fail("unauthorized terminal opened")
                assert error.value.response.status_code == status
            async with connect(url, additional_headers=CONSUMER_AUTH) as ws:
                assert await ws.recv() == b"ready\n"
                # The public lookup succeeds, but agent admission rejects a second attachment.
                with pytest.raises(InvalidStatus) as conflict:
                    async with connect(url, additional_headers=CONSUMER_AUTH):
                        pytest.fail("duplicate attachment accepted")
                assert conflict.value.response.status_code == 409
                # Each JSON chunk preserves the input Unicode through both hops.
                await ws.send(json.dumps({"type": "input", "data": "é🚀"}, ensure_ascii=False))
                assert await ws.recv() == "é🚀".encode()
                await ws.send(['{"type":"input","data":"', "é", '🚀"}'])
                assert await ws.recv() == "é🚀".encode()
                await ws.send('{"type":"resize","columns":500,"rows":300}')
                await ws.send('{"type":"input","data":"after resize"}')
                assert await ws.recv() == b"after resize"
                assert next(iter(daemon.terminals.values())).sizes == [(500, 300)]
                await client.delete(f"/v1/sandboxes/{identifier}")
                assert json.loads(await ws.recv()) == {"type": "status", "reason": "requested"}

    asyncio.run(exercise())


def test_agent_handshake_denials_are_typed_and_invalid_secret_is_dependency_failure(
    tmp_path: Path,
) -> None:
    from uuid import UUID, uuid4

    from boxie.domain.errors import BoxieError
    from boxie.domain.models import Principal
    from boxie.sandbox.client import AgentClient
    from boxie_test_support import Clock, DockerDaemon, configuration, live

    async def exercise() -> None:
        async with live(configuration(tmp_path), DockerDaemon(), Clock()) as (control, _agent):
            from api_test_support import api_server

            async with api_server(str(control.base_url)) as api:
                identifier = UUID(await create(api, await artifact(api)))
                await ready(api, str(identifier))
                for secret, principal, selected, status in (
                    (b"incorrect-agent-secret", Principal("one"), identifier, 503),
                    (b"agent-test-secret", Principal("two"), identifier, 404),
                    (b"agent-test-secret", Principal("one"), uuid4(), 404),
                    (b"agent-test-secret", Principal("!invalid"), identifier, 503),
                ):
                    async with AgentClient(str(control.base_url), secret) as network:
                        with pytest.raises(BoxieError) as denial:
                            async with network.attach(principal, selected):
                                pytest.fail("denied attachment opened")
                        assert denial.value.status == status
                        assert "secret" not in str(denial.value)
            async with api_server(str(control.base_url), b"incorrect-agent-secret") as api:
                assert (await api.get(f"/v1/sandboxes/{identifier}")).status_code == 503
                with pytest.raises(InvalidStatus) as upstream:
                    async with connect(
                        terminal_url(api, str(identifier)), additional_headers=CONSUMER_AUTH
                    ):
                        pytest.fail("invalid internal credential accepted")
                assert upstream.value.response.status_code == 503

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "frame",
    [
        b"raw",
        '{"type":"input","data":"' + "é" * 8192 + '"}',
        '{"type":"resize","columns":501,"rows":24}',
        '{"type":"input","data":"ok","extra":true}',
        '{"columns":80,"rows":24}',
        '{"type":"input","data":"\\ud800"}',
    ],
    ids=["binary", "oversized", "resize", "extra-field", "missing-type", "surrogate"],
)
def test_malformed_or_oversized_terminal_control_closes(tmp_path: Path, frame: str | bytes) -> None:
    async def exercise() -> None:
        async with stack(tmp_path) as (client, _agent, _daemon, _clock):
            identifier = await create(client, await artifact(client))
            await ready(client, identifier)
            async with connect(
                terminal_url(client, identifier), additional_headers=CONSUMER_AUTH
            ) as ws:
                assert await ws.recv() == b"ready\n"
                await ws.send(frame)
                with pytest.raises(ConnectionClosed) as error:
                    await asyncio.wait_for(ws.recv(), 1)
                assert error.value.rcvd is not None and error.value.rcvd.code == 1008

    asyncio.run(exercise())


def test_large_agent_output_is_chunked_without_unicode_loss(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with stack(tmp_path) as (client, _agent, daemon, _clock):
            identifier = await create(client, await artifact(client))
            await ready(client, identifier)
            async with connect(
                terminal_url(client, identifier), additional_headers=CONSUMER_AUTH
            ) as ws:
                assert await ws.recv() == b"ready\n"
                output = b"x" * 65535 + "🚀".encode() + b"end"
                next(iter(daemon.terminals.values())).queue.put_nowait(output)
                first = await asyncio.wait_for(ws.recv(), 1)
                second = await asyncio.wait_for(ws.recv(), 1)
                assert isinstance(first, bytes) and isinstance(second, bytes)
                assert len(first) == 65536 and first + second == output

    asyncio.run(exercise())


def test_disconnect_cancels_proxy_but_agent_drains_until_grace(tmp_path: Path) -> None:
    async def exercise() -> None:
        errors: list[dict[str, object]] = []
        asyncio.get_running_loop().set_exception_handler(
            lambda _loop, context: errors.append(context)
        )
        async with stack(tmp_path) as (client, agent, daemon, clock):
            identifier = await create(client, await artifact(client))
            await ready(client, identifier)
            async with connect(
                terminal_url(client, identifier), additional_headers=CONSUMER_AUTH
            ) as ws:
                assert await ws.recv() == b"ready\n"
            await asyncio.sleep(0.03)
            terminal = next(iter(daemon.terminals.values()))
            terminal.queue.put_nowait(b"still draining")
            await asyncio.sleep(0.03)
            assert terminal.queue.empty() and not terminal.closed and agent.reserved_count == 1
            clock.advance(119)
            await asyncio.sleep(0.03)
            assert agent.reserved_count == 1
            clock.advance(1)
            assert (await ready(client, identifier, "terminated"))["reason"] == "disconnected"
            assert terminal.closed and agent.reserved_count == 0
        assert errors == []

    asyncio.run(exercise())
