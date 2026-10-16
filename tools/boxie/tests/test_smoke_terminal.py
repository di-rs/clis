"""Shared native client respects attachment fencing across fresh connections."""

import asyncio
import json
from pathlib import Path

import pytest
from boxie_infra.terminal import Session
from boxie_test_support.api import CONSUMER_AUTH, artifact, create, ready, terminal_url
from boxie_test_support.terminal_network import network
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError


def test_session_default_is_single_attach_and_explicit_order_can_reconnect(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with network(tmp_path) as net:
            identifier = await create(net.api, await artifact(net.api))
            await ready(net.api, identifier)
            url = terminal_url(net.api, identifier)
            sent: list[dict[str, object]] = []
            for order in (None, 1, 2):
                async with connect(url, additional_headers=CONSUMER_AUTH) as socket:

                    async def send(value: str) -> None:
                        sent.append(json.loads(value))
                        await socket.send(value)

                    session = Session(socket.recv, send)
                    if order is None:
                        await asyncio.wait_for(session.start(), 2)
                    elif order == 1:
                        with pytest.raises(ConnectionClosedError):
                            await asyncio.wait_for(session.start(attachment_order=order), 2)
                    else:
                        await asyncio.wait_for(session.start(attachment_order=order), 2)
                        assert session.epoch == 2
            assert [item["attachment_order"] for item in sent if item["type"] == "attach"] == [
                1,
                1,
                2,
            ]

    asyncio.run(exercise())
