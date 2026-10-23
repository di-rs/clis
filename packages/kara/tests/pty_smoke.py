"""Run with python tests/pty_smoke.py /absolute/path/to/kara on Unix."""

import errno
import fcntl
import os
import pty
import pwd
import select
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import termios
import time
from pathlib import Path

from macos_acl import read_acl
from vt import screen_text


def acl(path: Path) -> bytes:
    if sys.platform == "darwin":
        return read_acl(path)
    try:
        return os.getxattr(path, "system.posix_acl_access")
    except OSError as error:
        if error.errno != errno.ENODATA:
            raise
        return b""


def restrict_acl(path: Path) -> None:
    if sys.platform == "darwin":
        subprocess.run(["chmod", "+a", "user:nobody deny read", str(path)], check=True)
    else:
        entries = [
            (1, 6, 0xFFFFFFFF),
            (2, 0, 65534),
            (4, 4, 0xFFFFFFFF),
            (16, 4, 0xFFFFFFFF),
            (32, 0, 0xFFFFFFFF),
        ]
        value = struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)
        os.setxattr(path, "system.posix_acl_access", value)


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
        assert reaped, f"editor did not exit: {bytes(captured)!r}"
        assert b"\x1b[?1049l" in captured, "alternate screen not restored"
        assert captured.rfind(b"\x1b[?7h") > captured.rfind(b"\x1b[?7l"), (
            "line wrapping not restored"
        )
        return bytes(captured) + b"\n" + screen_text(captured)
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
        write_only = directory / "write-only"
        write_only.write_bytes(b"KEEP\n")
        write_only.chmod(0o200)
        try:
            session(binary, directory, [b":w write-only\r", b"\x11"])
            assert stat.S_IMODE(write_only.stat().st_mode) == 0o200
        finally:
            write_only.chmod(0o600)
        assert write_only.read_bytes() == b"original\n", "save-as rejected a write-only destination"
        secured = directory / "secured"
        secured.write_bytes(b"KEEP\n")
        alternate_groups = [gid for gid in os.getgroups() if gid != directory.stat().st_gid]
        if alternate_groups:
            os.chown(secured, -1, alternate_groups[0])
        else:
            print("Skipping distinct-group case: no supplementary group available")
        secured.chmod(0o640)
        restrict_acl(secured)
        before = secured.stat()
        before_acl = acl(secured)
        assert before_acl, "restrictive ACL fixture is missing"
        session(binary, directory, [b":w secured\r", b"iS\x1b", b"\x13", b"\x11"])
        after = secured.stat()
        assert secured.read_bytes() == b"Soriginal\n"
        assert (after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode)) == (
            before.st_uid,
            before.st_gid,
            stat.S_IMODE(before.st_mode),
        ), "save changed file ownership or mode"
        after_acl = acl(secured)
        assert after_acl == before_acl, (
            f"save changed the access ACL: {before_acl!r} -> {after_acl!r}"
        )
        link = directory / "secured-link"
        link.symlink_to(secured)
        session(binary, directory, [b":w secured-link\r", b"\x11"])
        assert link.is_symlink(), "save replaced the symlink"
        assert secured.read_bytes() == b"original\n"
        assert secured.stat().st_gid == before.st_gid
        after_acl = acl(secured)
        assert after_acl == before_acl, (
            f"save through symlink changed the access ACL: {before_acl!r} -> {after_acl!r}"
        )
        inherited = directory / "inherited"
        inherited.mkdir()
        if sys.platform == "darwin":
            subprocess.run(
                ["chmod", "+a", "everyone allow read,file_inherit", str(inherited)], check=True
            )
        else:
            restrict_acl(inherited)
            os.setxattr(inherited, "system.posix_acl_default", acl(inherited))
            inherited.chmod(0o700)
        plain = inherited / "plain"
        plain.write_bytes(b"KEEP\n")
        if sys.platform == "darwin":
            subprocess.run(["chmod", "-N", str(plain)], check=True)
        else:
            os.removexattr(plain, "system.posix_acl_access")
        plain.chmod(0o600)
        session(binary, directory, [b":w inherited/plain\r", b"\x11"])
        assert plain.read_bytes() == b"original\n"
        assert not acl(plain), "replacement gained inherited access"
        session(binary, directory, [b":w inherited/new\r", b"\x11"])
        new = inherited / "new"
        assert new.read_bytes() == b"original\n"
        assert stat.S_IMODE(new.stat().st_mode) == 0o600
        assert not acl(new), "new private file gained inherited access"
        if sys.platform == "darwin":
            denied = directory / "denied"
            denied.write_bytes(b"KEEP\n")
            user = pwd.getpwuid(os.getuid()).pw_name
            subprocess.run(
                ["chmod", "+a", f"user:{user} deny write,append", str(denied)], check=True
            )
            try:
                captured = session(binary, directory, [b":w denied\r", b"iD\x1b", b"\x13", b"\x11"])
                assert denied.read_bytes() == b"KEEP\n", "save bypassed the write ACL"
                assert original.read_bytes() == b"Doriginal\n", (
                    "failed save changed active filename"
                )
                assert b"denied" in captured.lower()
            finally:
                subprocess.run(["chmod", "-N", str(denied)], check=True)
            original.write_bytes(b"original\n")
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
        for name, missing in (("dangling", "missing-target"), ("nested", "missing/target")):
            dangling = directory / name
            dangling.symlink_to(missing)
            captured = session(binary, directory, [f":w {name}\r".encode(), b"\x11"])
            assert dangling.is_symlink(), "replaced a dangling symlink"
            assert os.readlink(dangling) == missing
            assert not (directory / missing).exists()
            assert b"dangling symlink" in captured, "save error was not displayed"
        fifo = directory / "output.fifo"
        os.mkfifo(fifo)
        sock = directory / "output.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(sock))
            link = directory / "fifo-link"
            link.symlink_to(fifo)
            for destination in (fifo, sock, link, Path("/dev/null")):
                before = destination.stat()
                captured = session(binary, directory, [f":w {destination}\r".encode(), b"\x11"])
                after = destination.stat()
                assert (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode)) == (
                    before.st_dev,
                    before.st_ino,
                    stat.S_IFMT(before.st_mode),
                ), "replaced a special file"
                assert b"not a regular file" in captured, "save error was not displayed"
            assert link.is_symlink()
    print("Kara PTY save, failed-save recovery, and terminal cleanup checks passed")


if __name__ == "__main__":
    main()
