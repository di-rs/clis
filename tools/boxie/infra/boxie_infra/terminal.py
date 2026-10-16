"""Strict sequenced terminal consumer used by native smoke commands."""

import json
from collections.abc import Awaitable, Callable


class Session:
    def __init__(
        self,
        receive: Callable[[], Awaitable[bytes | str | None]],
        send: Callable[[str], Awaitable[None]],
    ) -> None:
        self.receive, self.send = receive, send
        self.epoch = 0
        self.pending = bytearray()

    async def _frame(self) -> dict[str, object] | None:
        frame = await self.receive()
        if frame is None:
            return {"type": "status"}
        if isinstance(frame, bytes):
            self.pending.extend(frame[8:])
            await self.send(
                json.dumps(
                    {
                        "type": "ack",
                        "epoch": self.epoch,
                        "sequence": int.from_bytes(frame[:8], "big"),
                    }
                )
            )
            return None
        value: dict[str, object] = json.loads(frame)
        return value

    async def start(self) -> None:
        await self.send(
            json.dumps(
                {"type": "attach", "attachment_order": 1, "resume_after": 0, "cols": 80, "rows": 24}
            )
        )
        initial = await self.receive()
        if initial is None:
            raise RuntimeError("terminal ended before attach")
        value = json.loads(initial)
        if value["type"] != "attached":
            raise RuntimeError("terminal did not attach")
        self.epoch = value["epoch"]
        while True:
            frame = await self._frame()
            if frame and frame["type"] == "replay_complete":
                break
            if frame and frame["type"] != "geometry":
                raise RuntimeError("terminal ended during replay")
        await self.resize(80, 24)

    async def read(self) -> bytes:
        while not self.pending:
            frame = await self._frame()
            if frame and frame["type"] == "status":
                return b""
        result = bytes(self.pending)
        self.pending.clear()
        return result

    async def write(self, data: bytes) -> None:
        await self.send(
            json.dumps(
                {"type": "input", "epoch": self.epoch, "source": "user", "data": data.decode()}
            )
        )

    async def resize(self, columns: int, rows: int) -> None:
        await self.send(
            json.dumps({"type": "resize", "epoch": self.epoch, "cols": columns, "rows": rows})
        )
        while True:
            frame = await self._frame()
            if frame and frame["type"] == "resized":
                return
            if frame and frame["type"] == "status":
                raise RuntimeError("terminal ended before resize")
