"""Single-event-loop bounded PTY history. Readers never hold up the PTY drain."""

import asyncio
from collections import deque
from dataclasses import dataclass

MAX_PAYLOAD = 16 * 1024
REPLAY_BYTES = 1024 * 1024
MAX_FRAMES = 4096


class StreamGap(ValueError):
    """The requested cursor has fallen outside retained output."""


@dataclass(frozen=True)
class OutputFrame:
    sequence: int
    data: bytes
    geometry: tuple[int, int] = (80, 24)

    def wire(self) -> bytes:
        return self.sequence.to_bytes(8, "big") + self.data


class ReplayBuffer:
    def __init__(self, capacity: int = REPLAY_BYTES) -> None:
        if capacity < MAX_PAYLOAD:
            raise ValueError("replay capacity must hold one frame")
        self.capacity = capacity
        self.geometry = (80, 24)
        self.frames: deque[OutputFrame] = deque()
        self.retained_bytes = 0
        self.tail = 0
        self.closed = False
        self.changed = asyncio.Event()

    def append(self, data: bytes) -> None:
        for offset in range(0, len(data), MAX_PAYLOAD):
            payload = data[offset : offset + MAX_PAYLOAD]
            self.tail += 1
            self.frames.append(OutputFrame(self.tail, payload, self.geometry))
            self.retained_bytes += len(payload)
            while self.retained_bytes > self.capacity or len(self.frames) > MAX_FRAMES:
                self.retained_bytes -= len(self.frames.popleft().data)
        self.changed.set()

    def resize(self, cols: int, rows: int) -> None:
        self.geometry = (cols, rows)
        self.changed.set()

    def after(self, cursor: int) -> list[OutputFrame]:
        if cursor < 0 or cursor > self.tail:
            raise ValueError("invalid output cursor")
        if self.frames and cursor < self.frames[0].sequence - 1:
            raise StreamGap("stream_gap")
        return [frame for frame in self.frames if frame.sequence > cursor]

    def close(self) -> None:
        self.closed = True
        self.changed.set()
