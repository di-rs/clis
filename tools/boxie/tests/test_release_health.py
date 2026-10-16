"""Release probes check authenticated dependencies without exposing credentials."""

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


@pytest.fixture
def origin() -> Iterator[tuple[str, list[str]]]:
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            seen.append(self.headers.get("Authorization", ""))
            self.send_response(200 if seen[-1] == "Bearer private-health-token" else 401)
            self.end_headers()
            self.wfile.write(json.dumps({"dependencies": {"docker": True}}).encode())

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", seen
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def test_agent_health_uses_private_credential_and_loopback(
    tmp_path: Path, origin: tuple[str, list[str]]
) -> None:
    from boxie.bootstrap.health import http_ready

    url, seen = origin
    secret = tmp_path / "credential"
    secret.write_text("private-health-token")
    secret.chmod(0o600)
    assert http_ready(url + "/readyz", secret)
    assert seen == ["Bearer private-health-token"]
    secret.write_text("wrong-health-token")
    assert not http_ready(url + "/readyz", secret)


def test_public_secret_permissions_are_rejected_before_probe(
    tmp_path: Path, origin: tuple[str, list[str]]
) -> None:
    from boxie.bootstrap.health import http_ready

    secret = tmp_path / "credential"
    secret.write_text("private-health-token")
    secret.chmod(0o644)
    with pytest.raises(ValueError, match="private"):
        http_ready(origin[0] + "/readyz", secret)
    assert origin[1] == []


def test_schema_probe_rejects_missing_foreign_or_modified_migrations(tmp_path: Path) -> None:
    from boxie.bootstrap.health import schema_current

    # SHA-256 of the literal empty migration; independent of the probe's scanner.
    checksum = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    migration = tmp_path / "001_initial.sql"
    migration.write_bytes(b"")
    assert schema_current({1: checksum}, tmp_path)
    assert not schema_current({}, tmp_path)
    assert not schema_current({1: checksum, 2: checksum}, tmp_path)
    migration.write_text("SELECT 1;")
    assert not schema_current({1: checksum}, tmp_path)
