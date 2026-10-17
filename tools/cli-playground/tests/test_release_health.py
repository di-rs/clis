"""Production readiness uses real peers without extending the public gateway API."""

import json
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playground.config import Settings

SECRET = "private-consumer-credential-never-log"


@contextmanager
def peer(
    payload: object,
    *,
    status: int = 200,
    authorization: str | None = None,
    redirect: str | None = None,
    slow_body: bool = False,
) -> Iterator[tuple[str, list[dict[str, str]]]]:
    requests: list[dict[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(dict(self.headers.items()))
            actual = (
                401
                if authorization and self.headers.get("Authorization") != authorization
                else status
            )
            self.send_response(actual)
            if redirect:
                self.send_header("Location", redirect)
            self.end_headers()
            body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
            try:
                if slow_body:
                    for byte in body:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.25)
                else:
                    self.wfile.write(body)
            except BrokenPipeError, ConnectionResetError:
                pass

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def settings(tmp_path: Path, upstream: str) -> Settings:
    credential = tmp_path / "consumer"
    credential.write_text(SECRET)
    credential.chmod(0o600)
    return Settings(
        data_dir=tmp_path / "data",
        public_url="https://play.example:8443",
        deploy_token="deployment-token",
        boxie_url=upstream,
        boxie_credential_file=credential,
    )


def test_health_checks_public_host_locally_and_authenticated_private_dependency(
    tmp_path: Path,
) -> None:
    from playground.health import check

    with (
        peer({"status": "ok"}) as (local, local_requests),
        peer(
            {"release": "a" * 40, "dependencies": {"agent": True, "orchestration": True}},
            authorization="Bearer " + SECRET,
        ) as (upstream, upstream_requests),
    ):
        check(settings(tmp_path, upstream), local_origin=local)
    assert local_requests[0]["Host"] == "play.example:8443"
    assert "Authorization" not in local_requests[0]
    assert upstream_requests[0]["Authorization"] == "Bearer " + SECRET


@pytest.mark.parametrize("dependencies", [{}, {"agent": False}, {"agent": 1}, [], None])
def test_nonready_or_malformed_dependencies_fail(tmp_path: Path, dependencies: object) -> None:
    from playground.health import HealthError, check

    with (
        peer({"status": "ok"}) as (local, _),
        peer({"dependencies": dependencies}) as (upstream, _),
    ):
        with pytest.raises(HealthError):
            check(settings(tmp_path, upstream), local_origin=local)


def test_bad_consumer_credential_fails_without_error_values(tmp_path: Path) -> None:
    from playground.health import HealthError, check

    with (
        peer({"status": "ok"}) as (local, _),
        peer(
            {"dependencies": {"agent": True}},
            authorization="Bearer different-secret",
        ) as (upstream, _),
    ):
        with pytest.raises(HealthError) as failure:
            check(settings(tmp_path, upstream), local_origin=local)
    assert SECRET not in str(failure.value)
    assert "different-secret" not in str(failure.value)


def test_redirect_cannot_forward_private_credential(tmp_path: Path) -> None:
    from playground.health import HealthError, check

    with (
        peer({"status": "ok"}) as (local, _),
        peer({"dependencies": {"agent": True}}) as (sink, forwarded),
        peer(
            {},
            status=302,
            redirect=sink + "/readyz",
        ) as (upstream, _),
    ):
        with pytest.raises(HealthError):
            check(settings(tmp_path, upstream), local_origin=local)
    assert forwarded == []


@pytest.mark.parametrize("body", [b"x" * 4097, b"not-json"], ids=["oversize", "invalid-json"])
def test_oversized_or_invalid_response_fails(tmp_path: Path, body: bytes) -> None:
    from playground.health import HealthError, check

    with peer({"status": "ok"}) as (local, _), peer(body) as (upstream, _):
        with pytest.raises(HealthError):
            check(settings(tmp_path, upstream), local_origin=local)


def test_unavailable_upstream_fails(tmp_path: Path) -> None:
    from playground.health import HealthError, check

    with peer({}) as (unavailable, _):
        pass
    with peer({"status": "ok"}) as (local, _):
        with pytest.raises(HealthError):
            check(settings(tmp_path, unavailable), local_origin=local)


def test_health_command_invalid_private_configuration_is_quiet() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "playground.health"], capture_output=True, check=False, timeout=10
    )
    assert result.returncode == 1
    assert result.stdout == b""
    assert result.stderr == b""


@pytest.mark.parametrize("slow_phase,limit", [("gateway", 3), ("upstream", 6)])
def test_slow_drip_response_cannot_extend_phase_deadlines(
    tmp_path: Path, slow_phase: str, limit: int
) -> None:
    from playground.health import HealthError, check

    with (
        peer({"status": "ok"}, slow_body=slow_phase == "gateway") as (local, _),
        peer({"dependencies": {"agent": True}}, slow_body=slow_phase == "upstream") as (
            upstream,
            _,
        ),
    ):
        started = time.monotonic()
        with pytest.raises(HealthError):
            check(settings(tmp_path, upstream), local_origin=local)
        assert time.monotonic() - started < limit
