"""Orchestrator-owned PTY reader and bounded sequenced output."""

import asyncio
from collections.abc import Awaitable, Callable

from boxie.domain.lifecycle import OrchestratorObservation
from boxie.domain.models import TerminationReason
from boxie.sandbox.docker import TerminalPort
from boxie.sandbox.terminal.replay import ReplayBuffer


class ExecutionTerminal:
    """PTY byte owner; durable attachment/activity decisions belong to the orchestrator."""

    def __init__(
        self,
        stream: TerminalPort,
        output_limit: int,
        terminate: Callable[[TerminationReason], Awaitable[OrchestratorObservation]],
    ) -> None:
        self.stream, self.remaining, self.terminate = stream, output_limit, terminate
        self.replay = ReplayBuffer()
        self.output_order = asyncio.Lock()
        self.reader: asyncio.Task[None] | None = None

    def start(self) -> None:
        self.reader = asyncio.create_task(self._read())

    async def _read(self) -> None:
        reason = TerminationReason.EXITED
        try:
            while data := await self.stream.read():
                accepted = data[: self.remaining]
                if accepted:
                    async with self.output_order:
                        self.replay.append(accepted)
                self.remaining -= len(accepted)
                if self.remaining == 0:
                    reason = TerminationReason.OUTPUT_LIMIT
                    break
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await self.terminate(reason)

    async def resize(self, cols: int, rows: int) -> None:
        # This serializes observed frame assignment with resize success, not the
        # kernel-time production of bytes already queued inside Docker.
        async with self.output_order:
            await self.stream.resize(cols, rows)
            self.replay.resize(cols, rows)

    async def close(self) -> None:
        if self.reader and self.reader is not asyncio.current_task():
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        try:
            await self.stream.close()
        finally:
            self.replay.close()
