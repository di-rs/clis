"""Regression cases for grapheme deletion and cursor boundaries."""

import sys
from pathlib import Path

from interaction import check


def main() -> None:
    binary = str(Path(sys.argv[1]).resolve())
    for initial, keys, expected in (
        (b"ab\n", b"d", b"b\n"),
        (b"ab\n", b"i\x1b[3~\x1b", b"b\n"),
        (b"ab\n", b"i\x7f\x1b", b"ab\n"),
        (b"ab\n", b"ki\x7f\x1b", b"b\n"),
        ("😀b\n".encode(), b"d", b"b\n"),
        ("e\u0301b\n".encode(), b"d", b"b\n"),
        (b"ab\ncd\n", b"ji\x7f\x1b", b"abcd\n"),
        (b"abcdef\nx\n123456\n", b"ejdiZ\x1b", b"abcdef\nxZ123456\n"),
        (b"abcd\nx", b"eGiX\x1b", b"abcd\nxX"),
    ):
        check(binary, initial, [keys, b"\x13", b"\x11"], expected)
    initial = ("x" * 4096 + "👩‍💻e\u0301\n").encode()
    expected = ("x" * 4096 + "👩‍💻\n").encode()
    check(binary, initial, [b"ei\x7f\x1b", b"\x13", b"\x11"], expected)
    check(binary, b"ab\n", [b"Gd", b"\x11"], b"ab\n")


if __name__ == "__main__":
    main()
