"""Publisher policies exercised through real SDK requests and HTTP streams."""

import gzip
import time
from collections.abc import AsyncIterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import anyio
import httpx
import preview_ci as publisher
import pytest
from githubkit import GitHub


class Body(httpx.AsyncByteStream):
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.closed = False

    async def __aiter__(self) -> AsyncIterator[bytes]:
        if self.mode == "compressed":
            yield gzip.compress(b"x" * 1000000)
        elif self.mode == "trickle":
            for _ in range(100):
                yield b"x"
                await anyio.sleep(0.01)
        else:
            yield b"x" * (17 if self.mode == "oversized" else 16)

    async def aclose(self) -> None:
        self.closed = True


@pytest.mark.parametrize("status", [200, 500])
@pytest.mark.parametrize("mode", ["oversized", "exact-limit", "trickle"])
def test_response_limits_deadlines_and_cleanup(mode: str, status: int) -> None:
    body = Body(mode)
    api = GitHub(
        "test-token",
        async_transport=httpx.MockTransport(lambda request: httpx.Response(status, stream=body)),
    )
    started = time.monotonic()
    if mode == "exact-limit" and status == 200:
        assert publisher.github_request(api, "user", limit=16, timeout=0.08) == b"x" * 16
    else:
        with pytest.raises(ValueError):
            publisher.github_request(api, "user", limit=16, timeout=0.08)
    assert time.monotonic() - started < 0.7
    assert body.closed


def test_redirected_artifact_does_not_forward_credentials() -> None:
    authorizations: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        authorizations.append(request.headers.get("authorization"))
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": "https://artifacts.example/archive"})
        return httpx.Response(200, content=b"archive")

    api = GitHub("test-token", async_transport=httpx.MockTransport(handle))
    assert publisher.github_request(api, "repos/owner/name/actions/artifacts/1/zip") == b"archive"
    assert authorizations == ["token test-token", None]


def test_pagination_retains_filters_and_fails_on_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        queries.append(dict(request.url.params))
        return httpx.Response(200, json=[{"id": i} for i in range(100)])

    monkeypatch.setattr(publisher, "MAX_PAGES", 2)
    api = GitHub("test-token", async_transport=httpx.MockTransport(handle))
    with pytest.raises(ValueError, match="pagination limit"):
        publisher.github_pages(
            api, "repos/owner/name/deployments?environment=cli-playground%2Fpr-7"
        )
    assert queries == [
        {"environment": "cli-playground/pr-7", "per_page": "100", "page": str(i)} for i in (1, 2)
    ]


def test_sdk_retries_and_caching_are_disabled_for_state_checks() -> None:
    requests: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request.method)
        return httpx.Response(500, json={"message": "failed"})

    api = GitHub("test-token", async_transport=httpx.MockTransport(handle))
    for _ in range(2):
        with pytest.raises(ValueError):
            publisher.github_request(
                api, "repos/owner/name/issues/1/comments", method="POST", body={"body": "test"}
            )
    assert requests == ["POST", "POST"]


def test_actual_http_stream_has_total_deadline() -> None:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            try:
                for _ in range(1000):
                    self.wfile.write(b"x")
                    self.wfile.flush()
                    time.sleep(0.01)
            except BrokenPipeError, ConnectionResetError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        api = GitHub(
            "test-token", base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False
        )
        started = time.monotonic()
        with pytest.raises(ValueError):
            publisher.github_request(api, "user", timeout=0.08)
        assert time.monotonic() - started < 0.7
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=1)


def test_token_environment_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "preferred")
    monkeypatch.setenv("GITHUB_TOKEN", "fallback")
    assert publisher.github_client().auth.token == "preferred"
    monkeypatch.delenv("GH_TOKEN")
    assert publisher.github_client().auth.token == "fallback"


@pytest.mark.parametrize("status", [200, 302, 500])
def test_unexpected_compression_is_rejected_before_buffering(status: int) -> None:
    body = Body("compressed")
    response = httpx.Response(status, headers={"content-encoding": "gzip"}, stream=body)

    def handle(request: httpx.Request) -> httpx.Response:
        return response

    api = GitHub("test-token", async_transport=httpx.MockTransport(handle))
    with pytest.raises(ValueError):
        publisher.github_request(api, "user", limit=2048)
    assert response.num_bytes_downloaded == 0
    assert body.closed
