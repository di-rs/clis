"""File type and syntax behavior observed through the executable."""

import sys
import tempfile
from pathlib import Path

from pty_smoke import session


def main() -> None:
    binary = str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory(prefix="kara-syntax-") as temp:
        directory = Path(temp)
        (directory / "original").write_bytes(b"let n = 12;\n")
        output = session(binary, directory, [b":w saved.RS\r", b"\x11"])
        assert b"| Text |" in output
        assert b"| Rust |" in output
        assert (directory / "saved.RS").read_bytes() == b"let n = 12;\n"
        output = session(binary, directory, [b":w missing/no.rs\r", b"\x11"])
        assert b"| Text |" in output
        assert b"| Rust |" not in output
        assert b"Could not save" in output


if __name__ == "__main__":
    main()
