"""Small binary and HTTP helpers shared by playground tests."""

import io
import os
import struct
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from playground.config import Settings as EnvironmentSettings
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource
from starlette.testclient import WebSocketTestSession


def elf_binary(payload: bytes = b"fixture") -> bytes:
    ident = b"\x7fELF\x02\x01\x01" + bytes(9)
    header = struct.pack("<HHIQQQIHHHHHH", 2, 62, 1, 0, 0, 0, 0, 64, 0, 0, 0, 0, 0)
    return ident + header + payload


def binary_bundle(binaries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in binaries.items():
            archive.writestr(name, content)
    return output.getvalue()


def upload(
    client: TestClient,
    content: bytes | None = None,
    *,
    cli: str = "echor",
    sha: str = "a" * 40,
    pr: int = 123,
    clis: dict[str, bytes] | None = None,
) -> dict[str, str]:
    response = client.post(
        "/api/environments",
        params={"sha": sha, "pr": pr},
        content=binary_bundle(clis or {cli: elf_binary() if content is None else content}),
        headers={"Authorization": "Bearer test-deployment-token"},
    )
    assert response.status_code == 201, response.text
    result: dict[str, str] = response.json()
    return result


APP_ENV = (
    b"PLAYGROUND_DOMAIN=play.example\nACME_EMAIL=ops@example.org\n"
    b"PLAYGROUND_DEPLOY_TOKEN=local-test-secret\n"
    b"CLOUDFLARE_DNS_API_TOKEN=private-renewal-token\n"
)
GATEWAY_IMAGE = "ghcr.io/di-rs/clis-playground-gateway@sha256:" + "1" * 64
SANDBOX_IMAGE = "registry.example:5000/team/sandbox@sha256:" + "2" * 64


def protected(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def git_commit(root: Path) -> None:
    """Commit staged fixture files without depending on the user's Git identity."""
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")} | {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.org",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-qm",
            "fixture",
        ],
        env=env,
        check=True,
    )


class Settings(EnvironmentSettings):
    """Validate explicit test configuration without reading the host environment."""

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings,)


@dataclass
class MutableClock:
    now: float = field(default_factory=time.time)

    def __call__(self) -> float:
        return self.now


AUTH = {"Authorization": "Bearer test-deployment-token"}


def build_event(
    client: TestClient,
    state: str,
    *,
    run: int = 10,
    attempt: int = 1,
    sha: str = "a" * 40,
    run_id: int | None = None,
) -> httpx.Response:
    response: httpx.Response = client.put(
        "/api/pull-requests/123/build",
        headers=AUTH,
        json={
            "sha": sha,
            "run_id": run_id or run * 100,
            "run_number": run,
            "run_attempt": attempt,
            "state": state,
        },
    )
    return response


def build_upload(
    client: TestClient,
    *,
    run: int = 10,
    attempt: int = 1,
    sha: str = "a" * 40,
    payload: bytes = b"fixture",
) -> httpx.Response:
    response: httpx.Response = client.post(
        "/api/environments",
        headers=AUTH,
        params={
            "pr": 123,
            "sha": sha,
            "run_id": run * 100,
            "run_number": run,
            "run_attempt": attempt,
        },
        content=binary_bundle({"echor": elf_binary(payload)}),
    )
    return response


def attach_terminal(socket: WebSocketTestSession) -> None:
    socket.send_json({"type": "attach", "resume_after": 0, "cols": 80, "rows": 24})
    assert socket.receive_json() == {"type": "attached", "epoch": 1}
    assert socket.receive_bytes() == (1).to_bytes(8, "big") + b"ready\n"
    assert socket.receive_json()["type"] == "replay_complete"
    socket.send_json({"type": "resize", "epoch": 1, "cols": 80, "rows": 24})
    assert socket.receive_json()["type"] == "resized"


ORIGIN = {"Origin": "https://play.example"}


def admission(client: TestClient, identifier: str, revision: str) -> dict[str, str]:
    response = client.post(
        f"/api/environments/{identifier}/sessions", headers=ORIGIN, json={"revision": revision}
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def socket_url(binding: dict[str, str]) -> str:
    return f"wss://play.example/ws/{binding['preview_id']}?session_id={binding['id']}&revision={binding['revision']}"
