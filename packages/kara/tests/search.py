"""Search interactions through the executable with independent saved-byte checks."""

import sys
from pathlib import Path

from interaction import check


def main() -> None:
    binary = str(Path(sys.argv[1]).resolve())
    output = check(binary, b"abc", [b"/query\x7f", b"\x1b", b"i/?", b"\x1b", b"\x13\x11"], b"/?abc")
    assert b"/quer" in output
    check(binary, b"abc", [b"/query\r", b"iX", b"\x1b", b":w\r:q\r"], b"Xabc")

    output = check(binary, "é hit hit".encode(), [b"/hit\r", b"\x11"], "é hit hit".encode())
    assert b"2 matches" in output

    check(
        binary,
        b"one target two",
        [b"/target", b"\r", b"iX", b"\x1b", b"\x13\x11"],
        b"one Xtarget two",
    )
    check(
        binary,
        b"one target two",
        [b"kk/target", b"\x1b", b"iX", b"\x1b", b"\x13\x11"],
        b"onXe target two",
    )
    check(
        binary, b"one target two", [b"/absent\r", b"iX", b"\x1b", b"\x13\x11"], b"Xone target two"
    )


if __name__ == "__main__":
    main()
