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

    check(binary, b"hit--hit", [b"kkkk/hit\r", b"iX", b"\x1b", b"\x13\x11"], b"hit--Xhit")
    check(binary, b"hit--hit", [b"e/hit\r", b"iX", b"\x1b", b"\x13\x11"], b"Xhit--hit")
    check(
        binary, b"hit--hit", [b"/hit", b"\x1b[B", b"\r", b"iX", b"\x1b", b"\x13\x11"], b"hit--Xhit"
    )

    check(binary, b"aaaaa", [b"/aa", b"\x1b[D", b"\r", b"iX", b"\x1b", b"\x13\x11"], b"aaXaaa")
    check(
        binary,
        b"hit--hit",
        [b"/hit", b"\x1b[B\x1b[A", b"\r", b"iX", b"\x1b", b"\x13\x11"],
        b"Xhit--hit",
    )

    check(
        binary, "e\u0301 e".encode(), [b"/e\r", b"iX", b"\x1b", b"\x13\x11"], "e\u0301 Xe".encode()
    )
    check(
        binary,
        "👩‍💻 👩".encode(),
        ["/👩\r".encode(), b"iX", b"\x1b", b"\x13\x11"],
        "👩‍💻 X👩".encode(),
    )
    check(
        binary,
        "🇧🇦🇦🇦".encode(),
        ["/🇦🇦\r".encode(), b"iX", b"\x1b", b"\x13\x11"],
        "🇧🇦X🇦🇦".encode(),
    )

    for keys, expected in (
        ([b"e?hit\r", b"n"], b"hit--Xhit--hit"),
        ([b"e?hit\r", b"N"], b"Xhit--hit--hit"),
        ([b"/hit\r", b"n"], b"hit--Xhit--hit"),
        ([b"/hit\r", b"N"], b"hit--hit--Xhit"),
        ([b"/hit\r", b"\x1b", b"n"], b"hit--Xhit--hit"),
        ([b"/hit\r", b"/\r"], b"hit--Xhit--hit"),
        ([b"/hit\r", b"?\r"], b"hit--hit--Xhit"),
        ([b"/hit\r", b"?absent", b"\x1b", b"n"], b"hit--Xhit--hit"),
    ):
        check(binary, b"hit--hit--hit", [*keys, b"iX", b"\x1b", b"\x13\x11"], expected)
    check(binary, b"hit--hit", [b"/hit\r", b"d", b"n", b"iX", b"\x1b", b"\x13\x11"], b"it--Xhit")


if __name__ == "__main__":
    main()
