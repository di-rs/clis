"""PTY regression driver invoked by the Rust CLI test."""

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

from vt import screen_text


def exercise(binary: str, exists: bool, wide: bool = False) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / ("界" * 10 if wide else "evil\x07\x1b[31m.txt")
        if exists:
            path.write_text("ordinary text\n")
        pid, master = pty.fork()
        if pid == 0:
            os.chdir(directory)
            os.execve(binary, [binary, path.name], os.environ | {"TERM": "xterm-256color"})
        fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 21 if wide else 120, 0, 0))
        finished = False
        output = bytearray()
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.1)[0]:
                    output.extend(os.read(master, 65536))
                expected = b"AVIGATE" if wide else b"ordinary text" if exists else b"Could not open"
                if expected in screen_text(output):
                    break
            assert b"\x1b[31m" not in output, repr(output)
            if wide:
                assert b"NAVIGATE" in screen_text(output), repr(output)
            else:
                assert b"\\u{7}\\u{1b}[31m.txt" in output, repr(output)
            if exists:
                assert b"ordinary text" in screen_text(output), repr(output)
                os.write(master, b"iX\x1b")
                time.sleep(0.1)
                os.write(master, b"\x13")
                time.sleep(0.1)
            os.write(master, b"\x11\x11")
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if select.select([master], [], [], 0.05)[0]:
                    try:
                        output.extend(os.read(master, 65536))
                    except OSError:
                        pass
                exited, status = os.waitpid(pid, os.WNOHANG)
                if exited:
                    finished = True
                    assert os.waitstatus_to_exitcode(status) == 0
                    break
            assert finished, repr(output)
            if exists:
                assert "X" in path.read_text()
        finally:
            os.close(master)
            if not finished:
                os.kill(pid, signal.SIGKILL)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if os.waitpid(pid, os.WNOHANG)[0]:
                        break
                    time.sleep(0.05)


for present in (True, False):
    exercise(sys.argv[1], present)
exercise(sys.argv[1], True, wide=True)
