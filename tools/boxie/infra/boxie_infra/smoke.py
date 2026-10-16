"""Exercise a generic text artifact through private HTTP/WS and inspect real runsc."""

import argparse
import asyncio
import hashlib
import io
import json
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
from websockets.asyncio.client import connect

from boxie_infra.terminal import Session


def containers(sandbox_id: str) -> list[str]:
    return subprocess.check_output(
        ["docker", "ps", "-aq", "--filter", f"label=boxie.sandbox={sandbox_id}"], text=True
    ).split()


def inspect_sandbox(sandbox_id: str) -> None:
    ids = containers(sandbox_id)
    if len(ids) != 1:
        raise RuntimeError("Expected exactly one owned sandbox container")
    data = json.loads(subprocess.check_output(["docker", "inspect", ids[0]], text=True))[0]
    host = data["HostConfig"]
    expected = {
        "Runtime": "runsc",
        "NetworkMode": "none",
        "ReadonlyRootfs": True,
        "Memory": 536870912,
        "MemorySwap": 536870912,
        "NanoCpus": 1000000000,
        "PidsLimit": 256,
        "CapDrop": ["ALL"],
    }
    for key, value in expected.items():
        if host[key] != value:
            raise RuntimeError(f"Sandbox security constraint failed: {key}")
    if data["Config"]["User"] != "65534:65534" or "no-new-privileges" not in host["SecurityOpt"]:
        raise RuntimeError("Sandbox identity or privilege constraint failed")
    if host["Ulimits"] != [{"Name": "nofile", "Soft": 1024, "Hard": 1024}]:
        raise RuntimeError("Sandbox file-descriptor limit failed")
    for target, size in (("/workspace", 67108864), ("/tmp", 16777216)):
        if f"size={size}" not in host["Tmpfs"][target]:
            raise RuntimeError("Sandbox tmpfs size constraint failed")
    mounts = data["Mounts"]
    if len(mounts) != 1 or mounts[0]["RW"] or mounts[0]["Destination"] != "/opt/artifact":
        raise RuntimeError("Sandbox artifact must be the only read-only bind")
    if host["LogConfig"]["Type"] != "none":
        raise RuntimeError("Sandbox Docker logs must be disabled")
    for target in ("/workspace", "/tmp"):
        if not all(option in host["Tmpfs"][target] for option in ("nosuid", "nodev", "noexec")):
            raise RuntimeError("Sandbox tmpfs constraint failed")


def verify_deleted(sandbox_id: str) -> None:
    if containers(sandbox_id):
        raise RuntimeError("Terminal stop did not delete the sandbox container")


async def exercise(url: str, credential_file: Path, profile: str) -> None:
    headers = {"Authorization": "Bearer " + credential_file.read_text().strip()}
    artifact, sandbox = str(uuid4()), None
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("message.txt", "generic-boxie-artifact\n")
    payload = archive.getvalue()
    async with httpx.AsyncClient(
        base_url=url, headers=headers, timeout=30, follow_redirects=False
    ) as client:
        response = await client.put(
            f"/v1/artifacts/{artifact}",
            content=payload,
            headers={"X-Content-SHA256": hashlib.sha256(payload).hexdigest()},
        )
        response.raise_for_status()
        try:
            response = await client.post(
                "/v1/sandboxes",
                headers={"Idempotency-Key": str(uuid4())},
                json={
                    "artifact_id": artifact,
                    "profile_id": profile,
                    "command": ["/bin/sh"],
                    "env": {},
                    "policy": {"max_seconds": 3600, "idle_seconds": 600, "disconnect_seconds": 120},
                },
            )
            response.raise_for_status()
            view: dict[str, Any] = response.json()
            sandbox = view["id"]
            async with asyncio.timeout(60):
                while view["state"] in {"accepted", "provisioning"}:
                    await asyncio.sleep(0.1)
                    response = await client.get(f"/v1/sandboxes/{sandbox}")
                    response.raise_for_status()
                    view = response.json()
            if view["state"] != "ready":
                raise RuntimeError(f"Generic sandbox failed: {view}")
            inspect_sandbox(sandbox)
            endpoint = url.replace("http://", "ws://").replace("https://", "wss://")
            async with connect(
                f"{endpoint}/v1/sandboxes/{sandbox}/terminal",
                additional_headers=headers,
                proxy=None,
            ) as terminal:
                # Split marker in the command: echoed input cannot satisfy the output assertion.
                token = uuid4().hex
                session = Session(terminal.recv, terminal.send)
                await session.start()
                await session.write(
                    f"cat /opt/artifact/message.txt; printf '%s%s' '{token[:16]}' '{token[16:]}'\n".encode()
                )
                output = b""
                async with asyncio.timeout(20):
                    while token.encode() not in output:
                        frame = await session.read()
                        if not isinstance(frame, bytes):
                            raise RuntimeError("Unexpected terminal status")
                        output += frame
                        if len(output) > 65536:
                            raise RuntimeError("Excessive terminal output")
                if b"generic-boxie-artifact" not in output:
                    raise RuntimeError("Generic artifact was not readable in sandbox")
        finally:
            if sandbox is not None:
                response = await client.delete(f"/v1/sandboxes/{sandbox}")
                response.raise_for_status()
                async with asyncio.timeout(30):
                    while response.json()["cleanup_pending"] or response.json()["state"] not in {
                        "terminated",
                        "failed",
                    }:
                        await asyncio.sleep(0.1)
                        response = await client.get(f"/v1/sandboxes/{sandbox}")
                        response.raise_for_status()
                verify_deleted(sandbox)
            response = await client.delete(f"/v1/artifacts/{artifact}")
            response.raise_for_status()
    print(
        "PASS: generic artifact, private HTTP/WS terminal, real runsc security, confirmed deletion"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--boxie-url", required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--profile", default="smoke")
    args = parser.parse_args()
    asyncio.run(exercise(args.boxie_url, args.credential_file, args.profile))


if __name__ == "__main__":
    main()
