"""Run with python tests/pty_smoke.py /absolute/path/to/kara on Unix."""

import fcntl
import os
import pty
import select
import signal
import struct
import sys
import tempfile
import termios
import time
from pathlib import Path


def session(binary: str, directory: Path, keys: list[bytes]) -> bytes:
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(directory)
        os.environ.update(
            TERM="xterm-256color", SANCTUARY_LOG_LEVEL="off", SANCTUARY_LOG_STDERR="off"
        )
        os.execv(binary, [binary, "original"])
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 120, 0, 0))
    captured = bytearray()

    def drain() -> None:
        deadline = time.monotonic() + 0.3
        while time.monotonic() < deadline:
            if select.select([fd], [], [], 0.02)[0]:
                try:
                    captured.extend(os.read(fd, 65536))
                except OSError:
                    return

    reaped = False
    try:
        drain()
        for key in keys:
            os.write(fd, key)
            drain()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done:
                reaped = True
                assert os.waitstatus_to_exitcode(status) == 0
                break
            drain()
        assert reaped, "editor did not exit"
        assert b"\x1b[?1049l" in captured, "alternate screen not restored"
        assert captured.rfind(b"\x1b[?7h") > captured.rfind(b"\x1b[?7l"), (
            "line wrapping not restored"
        )
        return bytes(captured)
    finally:
        if not reaped:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
        os.close(fd)


def main() -> None:
    binary = str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory(prefix="kara-pty-") as temp:
        directory = Path(temp)
        original = directory / "original"
        target = directory / "other file"
        original.write_bytes(b"original\n")
        target.write_bytes(b"KEEP\n")
        session(binary, directory, [b":w other file\r", b"iX\x1b", b"\x13", b"\x11"])
        assert original.read_bytes() == b"original\n"
        assert target.read_bytes() == b"Xoriginal\n"
        session(binary, directory, [b"iY\x1b", b":w missing/target\r", b"\x13", b"\x11"])
        assert original.read_bytes() == b"Yoriginal\n", "failed save changed buffer identity"
        assert not (directory / "missing").exists()
        target.chmod(0o444)
        try:
            captured = session(binary, directory, [b":w other file\r", b"\x11"])
            assert target.read_bytes() == b"Xoriginal\n", "overwrote read-only file"
            assert b"denied" in captured.lower(), "save error was not displayed"
        finally:
            target.chmod(0o644)
    print("Kara PTY save, failed-save recovery, and terminal cleanup checks passed")


if __name__ == "__main__":
    main()
