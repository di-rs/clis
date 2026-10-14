"""Small binary and HTTP helpers shared by playground tests."""

import io
import struct
import subprocess
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient


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


APP_ENV = b"PLAYGROUND_DOMAIN=play.example\nDNS_PROVIDER=cloudflare\nACME_EMAIL=ops@example.org\nPLAYGROUND_DEPLOY_TOKEN=local-test-secret\n"
DNS_ENV = b"CF_DNS_API_TOKEN=raw-$credential\n"
GATEWAY_IMAGE = "ghcr.io/di-rs/clis-playground-gateway@sha256:" + "1" * 64
SANDBOX_IMAGE = "registry.example:5000/team/sandbox@sha256:" + "2" * 64


def protected(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    path.chmod(0o600)
    return path


def git_commit(root: Path) -> None:
    """Commit staged fixture files without depending on the user's Git identity."""
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
        check=True,
    )
