"""Observable modal editing regressions, with independently worked saved bytes."""

import sys
import tempfile
from pathlib import Path

from pty_smoke import session


def check(binary: str, initial: bytes, keys: list[bytes], expected: bytes) -> bytes:
    with tempfile.TemporaryDirectory(prefix="kara-interaction-") as temp:
        directory = Path(temp)
        original = directory / "original"
        original.write_bytes(initial)
        output = session(binary, directory, keys)
        assert original.read_bytes() == expected, (keys, original.read_bytes(), expected)
        return output


def main() -> None:
    binary = str(Path(sys.argv[1]).resolve())
    # Every expected value describes the user's edits, independently of rendering.
    for motion, expected in (
        (b"jk", b"ab\ncXd\n"),
        (b"ju", b"Xab\ncd\n"),
        (b"kh", b"Xab\ncd\n"),
        (b"e", b"abX\ncd\n"),
        (b"Ge", b"ab\ncdX\n"),
        (b"jg", b"Xab\ncd\n"),
        (b"\x1b[B\x1b[C", b"ab\ncXd\n"),
    ):
        check(binary, b"ab\ncd\n", [motion + b"iX\x1b", b"\x13", b"\x11"], expected)
    for placement, expected in (
        (b"i", b"X  ab\n"),
        (b"a", b" X ab\n"),
        (b"I", b"  Xab\n"),
        (b"A", b"  abX\n"),
    ):
        check(binary, b"  ab\n", [placement + b"X\x1b", b"\x13", b"\x11"], expected)
    check(binary, "a😀b\n".encode(), [b"kiX\x03", b":w\r", b":q\r"], "aX😀b\n".encode())
    output = check(binary, b"ab\n", [b"iX\x1b", b"\x11", b"h", b"\x11", b"\x11"], b"ab\n")
    assert output.count(b"File has unsaved changes") >= 2
    check(binary, b"ab\n", [b"iX\x1b", b":q!\r"], b"ab\n")


if __name__ == "__main__":
    main()
