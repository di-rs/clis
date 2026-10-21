"""Exercise chunked bodies at the ASGI boundary, before JSON parsing."""

import asyncio

import pytest
from cli_playground.transport import RequestBodyBoundary
from starlette.types import Message, Receive, Scope, Send


@pytest.mark.parametrize(
    "path", ["/api/environments/unknown/sessions", "/api/pull-requests/1/build"]
)
@pytest.mark.parametrize("headers", [[], [(b"content-length", b"1")]])
def test_oversized_body_stops_before_parser(path: str, headers: list[tuple[bytes, bytes]]) -> None:
    async def exercise() -> None:
        consumed = 0
        output: list[Message] = []

        async def receive() -> Message:
            nonlocal consumed
            consumed += 1
            assert consumed <= 3
            return {"type": "http.request", "body": b" " * 8192, "more_body": True}

        async def parser(scope: Scope, receive: Receive, send: Send) -> None:
            pytest.fail("oversized body reached parser")

        async def send(message: Message) -> None:
            output.append(message)

        await RequestBodyBoundary(parser)(
            {"type": "http", "method": "POST", "path": path, "headers": headers}, receive, send
        )
        assert output[0]["status"] == 413
        assert consumed == 3

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "path,size", [("/api/pull-requests/1/build", 16384), ("/api/environments", 32768)]
)
def test_valid_body_and_streaming_upload_are_preserved(path: str, size: int) -> None:
    async def exercise() -> None:
        messages: list[Message] = [
            {"type": "http.request", "body": b"x" * (size // 2), "more_body": True},
            {"type": "http.request", "body": b"y" * (size // 2), "more_body": False},
        ]

        async def receive() -> Message:
            return messages.pop(0)

        async def parser(scope: Scope, receive: Receive, send: Send) -> None:
            body = bytearray()
            while True:
                message = await receive()
                body.extend(message["body"])
                if not message.get("more_body", False):
                    break
            assert body == b"x" * (size // 2) + b"y" * (size // 2)

        async def send(message: Message) -> None:
            pytest.fail("unexpected response")

        await RequestBodyBoundary(parser)(
            {"type": "http", "method": "POST", "path": path}, receive, send
        )

    asyncio.run(exercise())
