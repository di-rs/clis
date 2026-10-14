r"""Exercise the production PTY/session code against an installed Docker + gVisor host.

From the repository root, provide an existing Linux x86_64 echor binary:
  uv run --locked --all-packages --no-dev --directory tools/cli-playground \
    python -m infra.smoke --image clis-playground-sandbox:local --binary /absolute/path/echor

This creates one disposable session. It never changes Docker runtime configuration,
uses gateway credentials, or stops other containers.
"""

import argparse
import asyncio
import json
import re
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

from aiodocker import Docker
from aiohttp import ClientTimeout
from playground.config import Settings
from playground.models import Environment
from playground.sessions import DockerSession, SessionManager
from playground.storage import validate_elf

PROMPT = b"__PLAYGROUND_SMOKE_PROMPT__ "
ANSI = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")


class Shell:
    def __init__(self, session: DockerSession) -> None:
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


async def exercise(binary: Path, image: str, runtime: str) -> None:
    binary = binary.resolve(strict=True)
    validate_elf(binary)
    with tempfile.TemporaryDirectory(prefix="clis-playground-smoke-") as temporary:
        directory = Path(temporary)
        binary_dir = directory / "bin"
        binary_dir.mkdir(mode=0o755)
        executable = binary_dir / "echor"
        shutil.copyfile(binary, executable)
        executable.chmod(0o555)
        settings = Settings(
            data_dir=directory,
            public_url="http://localhost",
            deploy_token="unused-smoke-test",
            sandbox_image=image,
            sandbox_runtime=runtime,
            session_seconds=90,
        )
        environment = Environment(
            id=uuid.uuid4().hex,
            clis=["echor"],
            sha="0" * 40,
            pr=1,
            expires_at=time.time() + 90,
            binary_dir=binary_dir,
        )
        manager = SessionManager(settings)
        name: str | None = None
        try:
            async with manager.open(environment) as session:
                name = session.name
                shell = Shell(session)
                await shell.start()
                async with Docker(timeout=ClientTimeout(total=20)) as docker:
                    details = await docker.containers.container(name).show()
                actual_runtime = details["HostConfig"]["Runtime"]
                if actual_runtime != runtime:
                    raise RuntimeError(f"Expected {runtime}, got {actual_runtime!r}")

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
            # SessionManager performs its production cancellation-safe Docker removal.
            # Retrying here also covers failures during container creation/attachment.
            await manager.close()
        if name is not None:
            async with Docker(timeout=ClientTimeout(total=20)) as docker:
                remaining = await docker.containers.list(
                    all=True, filters=json.dumps({"name": [name]})
                )
            if remaining or manager.active:
                raise RuntimeError(f"Smoke session was not removed: {name}")
    print(
        f"PASS: {runtime}, native CLI, sample files, pipes/redirection, read-only root, resize, Ctrl-C, cleanup"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--binary", type=Path, required=True, help="Linux x86_64 echor executable")
    parser.add_argument(
        "--image", default="clis-playground-sandbox:local", help="existing sandbox image"
    )
    parser.add_argument(
        "--runtime",
        choices=("runsc", "runc"),
        default="runsc",
        help="runsc in production; runc is an explicit local Docker Desktop override",
    )
    arguments = parser.parse_args()
    try:
        asyncio.run(exercise(arguments.binary, arguments.image, arguments.runtime))
    except (OSError, RuntimeError, ValueError, TimeoutError) as error:
        print(f"Sandbox smoke failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
