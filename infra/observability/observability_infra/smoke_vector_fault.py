"""One bounded, concurrent Vector fault diagnostic reader; no raw evidence export."""

import contextlib
import os
import selectors
import signal
import subprocess
import threading
from types import TracebackType
from typing import Any, Self

from observability_infra.smoke_runtime import Deadline


class VectorDiagnostics:
    """Drain while the scenario allocates/probes; retain only cumulative fixed flags."""

    def __init__(self, args: list[str], env: dict[str, str], deadline: Deadline) -> None:
        self.args, self.env, self.deadline = args, env, deadline
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.data: dict[str, Any] = {
            "errno_text_observed": False,
            "writer_unrecoverable_observed": False,
            "bytes": 0,
            "stderr_bytes": 0,
            "lines": 0,
            "eof": False,
            "error_type": None,
            "reaped": False,
            "raw_output_exported": False,
        }

    def __enter__(self) -> Self:
        self.deadline.remaining()
        self.process = subprocess.Popen(
            self.args,
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.thread = threading.Thread(target=self.read, name="vector-fault-diagnostics")
        try:
            self.thread.start()
        except BaseException:
            self.reap()
            raise
        return self

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return dict(self.data)

    def check(self) -> None:
        if self.snapshot()["error_type"] is not None:
            raise RuntimeError("Vector diagnostic reader failed")

    def read(self) -> None:
        assert self.process.stdout is not None
        pending = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(self.process.stdout, selectors.EVENT_READ)
                assert self.process.stderr is not None
                selector.register(self.process.stderr, selectors.EVENT_READ, data="stderr")
                while not self.stop.is_set():
                    remaining = self.deadline.remaining()
                    for key, _ in selector.select(min(remaining, 0.05)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fd)
                            if key.data != "stderr" and pending:
                                raise RuntimeError("Vector diagnostic line incomplete")
                            continue
                        if key.data == "stderr":
                            with self.lock:
                                self.data["stderr_bytes"] += len(chunk)
                            raise RuntimeError("Vector journal reader diagnostic")
                        pending.extend(chunk)
                        with self.lock:
                            self.data["bytes"] += len(chunk)
                            if self.data["bytes"] > 262144:
                                raise RuntimeError("Vector diagnostic byte limit")
                            while (end := pending.find(b"\n")) >= 0:
                                if end > 65535 or self.data["lines"] >= 4096:
                                    raise RuntimeError("Vector diagnostic line limit")
                                line = bytes(pending[:end])
                                del pending[: end + 1]
                                self.data["lines"] += 1
                                self.data["errno_text_observed"] |= (
                                    b"No space left on device" in line or b"os error 28" in line
                                )
                                self.data["writer_unrecoverable_observed"] |= (
                                    b"Disk buffer writer has encountered an unrecoverable error."
                                    in line
                                )
                            if len(pending) > 65536:
                                raise RuntimeError("Vector diagnostic line limit")
                    if not selector.get_map():
                        # Closed streams do not imply child exit; remain cancellable.
                        while (code := self.process.poll()) is None:
                            if self.stop.wait(min(0.05, self.deadline.remaining())):
                                return
                        if code != 0:
                            raise RuntimeError("Vector diagnostic command failed")
                        with self.lock:
                            self.data["eof"] = True
                        return
        except Exception as error:
            with self.lock:
                self.data["error_type"] = type(error).__name__
        finally:
            try:
                self.reap()
            except Exception as error:
                with self.lock:
                    self.data["error_type"] = type(error).__name__

    def reap(self) -> None:
        self.process.poll()  # Reap an exited leader before signalling its remaining group.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.process.pid, signal.SIGKILL)
        self.process.wait(timeout=1)
        assert self.process.stdout is not None
        self.process.stdout.close()
        assert self.process.stderr is not None
        self.process.stderr.close()
        with self.lock:
            self.data["reaped"] = True

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.stop.set()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise RuntimeError("Vector diagnostic reader did not exit")
        if kind is None:
            self.check()
