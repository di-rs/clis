"""Small binary and HTTP helpers shared by playground tests."""

import io
import struct
import zipfile

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
