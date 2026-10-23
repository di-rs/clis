"""Byte-preserving UTF-8 file I/O through the interactive executable."""

import sys
from pathlib import Path

from interaction import check


def main() -> None:
    binary = str(Path(sys.argv[1]).resolve())
    for initial in (
        b"no final newline",
        b"",
        b"\n",
        b"one\n\n",
        b"one\r\ntwo\r\n",
        b"one\r\ntwo\nthree",
        b"standalone\rcarriage return\n",
        b"\xef\xbb\xbfUTF-8 signature\n",
        b"line\n" * 100_000,
    ):
        check(binary, initial, [b"\x13", b"\x11"], initial)
    check(binary, b"ab", [b"ei\rX\x1b", b"\x13", b"\x11"], b"ab\nX")
    check(
        binary,
        b"ab\r\ncd\r\n",
        [b"ki\rX\x1b", b"\x13", b"\x11"],
        b"a\r\nXb\r\ncd\r\n",
    )
    check(binary, b"ab\r\ncd", [b"ji\x7f\x1b", b"\x13", b"\x11"], b"abcd")
    output = check(binary, b"\xff\xfe", [b"\x11"], b"\xff\xfe")
    assert b"Could not open" in output


if __name__ == "__main__":
    main()
