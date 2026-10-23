"""Observe ASCII screen rows in PTY output, including cursor-addressed updates.

This intentionally handles only the VT commands needed by these assertions.
Raw bytes remain available for terminal-control and restoration assertions.
"""

import re

TOKEN = re.compile(rb"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b\[([0-9;?]*)([ -/]*)([@-~])|([^\x1b]+)")


def screen_text(output: bytes | bytearray) -> bytes:
    rows: dict[int, bytearray] = {}
    history: list[bytes] = []
    x, y = 0, 0

    def snapshot() -> None:
        if y in rows:
            history.append(bytes(rows[y]).rstrip())

    for token in TOKEN.finditer(output):
        parameters, _, command, plain = token.groups()
        if command:
            snapshot()
            values = [int(part or b"0") for part in parameters.lstrip(b"?").split(b";")]
            if command in (b"H", b"f"):
                y = max(0, values[0] - 1)
                x = max(0, (values[1] if len(values) > 1 else 1) - 1)
            elif command == b"J" and values[0] in (2, 3):
                rows.clear()
            elif command == b"K":
                row = rows.setdefault(y, bytearray())
                if values[0] == 2:
                    row.clear()
                elif values[0] == 0:
                    del row[x:]
            elif command == b"G":
                x = max(0, values[0] - 1)
            elif command in (b"A", b"B", b"C", b"D"):
                step = values[0] or 1
                if command == b"A":
                    y = max(0, y - step)
                elif command == b"B":
                    y += step
                elif command == b"C":
                    x += step
                else:
                    x = max(0, x - step)
        elif plain:
            for byte in plain:
                if byte == 13:
                    snapshot()
                    x = 0
                elif byte == 10:
                    snapshot()
                    y += 1
                elif byte >= 32:
                    row = rows.setdefault(y, bytearray())
                    row.extend(b" " * max(0, x + 1 - len(row)))
                    row[x] = byte
                    x += 1
    snapshot()
    return b"\n".join(history)
