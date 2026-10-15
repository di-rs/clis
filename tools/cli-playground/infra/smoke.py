"""Exercise an approved Playground shell profile through Boxie's private API.

Run against a disposable local Boxie stack with an existing Linux x86_64 echor
binary, --boxie-url, and --credential-file. This does not configure Docker.
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path

from playground.boxie_client import HttpBoxieClient, SandboxRequest, TerminalPort
from playground.storage import validate_elf

PROMPT = b"__PLAYGROUND_SMOKE_PROMPT__ "
ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")


class Session:
    def __init__(self, terminal: TerminalPort) -> None:
        self.terminal = terminal

    async def read(self) -> bytes:
        data = await self.terminal.receive()
        return data if isinstance(data, bytes) else b""

    async def write(self, data: bytes) -> None:
        await self.terminal.send(json.dumps({"type": "input", "data": data.decode()}))

    async def resize(self, columns: int, rows: int) -> None:
        await self.terminal.send(json.dumps({"type": "resize", "columns": columns, "rows": rows}))


class Shell:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.pending = b""

    async def until(self, marker: bytes, seconds: float = 10) -> bytes:
        try:
            async with asyncio.timeout(seconds):
                while marker not in self.pending:
                    data = await self.session.read()
                    if not data:
                        raise RuntimeError(f"Sandbox exited unexpectedly: {self.pending[-4096:]!r}")
                    self.pending += data
                    if len(self.pending) > 256 * 1024:
                        raise RuntimeError("Smoke command exceeded the terminal output limit")
        except TimeoutError:
            raise RuntimeError(
                f"Timed out waiting for shell output: {self.pending[-4096:]!r}"
            ) from None
        output, self.pending = self.pending.split(marker, 1)
        return ANSI.sub(b"", output).replace(b"\r", b"")

    async def start(self) -> None:
        await self.until(b" $ ", seconds=30)
        # Disable command echo so an echoed assertion cannot masquerade as its output.
        await self.session.write(
            b"stty -echo; bind 'set enable-bracketed-paste off'; "
            b"PS1='__PLAYGROUND_SMOKE_PROMPT__ '\n"
        )
        # The initial line is echoed, including the new prompt; first consume its newline.
        await self.until(b"\n")
        await self.until(PROMPT)

    async def run(self, command: str) -> bytes:
        token = uuid.uuid4().hex
        await self.session.write(
            (
                f"{{ {command}; }}; __smoke_status=$?; "
                f"printf '\\036{token}:%s\\037' \"$__smoke_status\"\n"
            ).encode()
        )
        output = await self.until(b"\x1e" + token.encode() + b":")
        status = await self.until(b"\x1f")
        await self.until(PROMPT)
        if status != b"0":
            raise RuntimeError(f"Smoke command failed ({status!r}): {command}\n{output!r}")
        return output


async def exercise(binary: Path, url: str, credential_file: Path, profile: str) -> None:
    binary = binary.resolve(strict=True)
    validate_elf(binary)
    client = HttpBoxieClient(url, credential_file.read_text().strip())
    artifact_id = uuid.uuid4()
    sandbox_id = None
    try:
        with tempfile.TemporaryDirectory(prefix="clis-playground-smoke-") as temporary:
            archive = Path(temporary) / "bundle.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.write(binary, "echor")
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            await client.upload(artifact_id, digest, archive)
            view = await client.create(
                str(uuid.uuid4()), SandboxRequest(artifact_id=artifact_id, profile_id=profile)
            )
            sandbox_id = view.id
            async with asyncio.timeout(30):
                while view.state in {"accepted", "provisioning"}:
                    await asyncio.sleep(0.1)
                    view = await client.get(sandbox_id)
            if view.state != "ready":
                raise RuntimeError(f"Sandbox did not become ready: {view.state}")
            async with client.terminal(sandbox_id) as terminal:
                session = Session(terminal)
                shell = Shell(session)
                await shell.start()
                output = await shell.run("echor 'native-cli-ok'")
                if output.strip() != b"native-cli-ok":
                    raise RuntimeError(f"Unexpected Rust CLI output: {output!r}")
                await shell.run(
                    "test -f welcome.txt && test -f nested/notes.txt && "
                    "grep -q 'disposable workspace' welcome.txt && "
                    "printf 'one\\ntwo\\n' > smoke.txt && "
                    'test "$(cat smoke.txt | wc -l)" -eq 2 && '
                    "printf 'three\\n' >> smoke.txt && test \"$(wc -l < smoke.txt)\" -eq 3"
                )
                await shell.run(
                    'test "$(id -u)" -eq 65534 && '
                    "test ! -e /var/run/docker.sock && "
                    "! touch /etc/playground-smoke 2>/dev/null"
                )

                await session.resize(101, 37)
                async with asyncio.timeout(10):
                    while (await shell.run("stty size")).strip() != b"37 101":
                        await asyncio.sleep(0.1)

                # Wait until sleep is in the foreground before delivering the terminal Ctrl-C.
                await session.write(b"printf '\\036sleeping\\037'; sleep 30\n")
                await shell.until(b"\x1esleeping\x1f")
                await asyncio.sleep(0.1)
                await session.write(b"\x03")
                await shell.until(PROMPT)
                await shell.run('test "$?" -eq 130')
    finally:
        if sandbox_id is not None:
            view = await client.terminate(sandbox_id)
            async with asyncio.timeout(30):
                while view.cleanup_pending or view.state not in {"terminated", "failed"}:
                    await asyncio.sleep(0.1)
                    view = await client.get(sandbox_id)
        await client.retire(artifact_id)
        await client.close()
    print(
        "PASS: private API, native CLI, sample files, pipes/redirection, read-only root, resize, Ctrl-C, cleanup"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--binary", type=Path, required=True, help="Linux x86_64 echor executable")
    parser.add_argument("--boxie-url", required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--profile", default="playground")
    arguments = parser.parse_args()
    try:
        asyncio.run(
            exercise(
                arguments.binary, arguments.boxie_url, arguments.credential_file, arguments.profile
            )
        )
    except (OSError, RuntimeError, ValueError, TimeoutError) as error:
        print(f"Sandbox smoke failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
