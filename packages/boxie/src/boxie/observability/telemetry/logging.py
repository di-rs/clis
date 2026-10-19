"""Sanitize before a bounded stdlib logging queue; one daemon owns stdout delivery."""

import asyncio
import atexit
import json
import logging
import math
import os
import queue
import re
import sys
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener
from types import TracebackType
from typing import Any, cast
from uuid import uuid4

from httpx import URL
from pydantic import BaseModel, ConfigDict

from boxie.observability.telemetry.context import current_context, validated

# Finite categories, no URLs, headers, exception messages, notes, source or locals.
_BUILTIN_ERRORS = frozenset(
    {
        "ValueError",
        "TypeError",
        "KeyError",
        "RuntimeError",
        "OSError",
        "TimeoutError",
        "ConnectionError",
        "PermissionError",
        "FileNotFoundError",
        "CancelledError",
        "AssertionError",
        "SystemExit",
        "KeyboardInterrupt",
    }
)
_BASE_EVENTS = {
    "foreign_log": "Dependency diagnostic",
    "process_failed": "Process failed",
    "thread_failed": "Thread failed",
    "async_failed": "Asynchronous operation failed",
    "unraisable_failed": "Unraisable operation failed",
    "request.completed": "Request completed",
}
_LEVELS = {10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR", 50: "CRITICAL"}


class LogSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    service: str
    role: str
    release: str = "unknown"
    instance_id: str = ""  # A generated process UUID; not hostname/environment content.
    level: int = logging.INFO


@dataclass
class LogStats:
    queued_records: int = 0
    dropped_count: int = 0
    configuration_errors: int = 0
    sink_state: str = "unavailable"
    closed: bool = False


def _logger_category(name: object) -> str:
    if type(name) is not str or len(name) > 128:
        return "dependency"
    for prefix in ("boxie", "uvicorn", "temporalio"):
        if name == prefix or name.startswith(prefix + "."):
            return prefix
    return "dependency"


def _exception(error: BaseException) -> dict[str, object]:
    chain: list[str] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and len(chain) < 4 and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        kind = name if name in _BUILTIN_ERRORS else "Exception"
        frames: list[str] = []
        tb = current.__traceback__
        while tb is not None and len(frames) < 4:
            # Module category and line identify a frame without filenames/source/locals.
            module = tb.tb_frame.f_globals.get("__name__")
            frames.append(f"{_logger_category(module)}:{min(tb.tb_lineno, 999999)}")
            tb = tb.tb_next
        chain.append(kind + ("@" + ",".join(frames) if frames else ""))
        current = current.__cause__ or (
            None if current.__suppress_context__ else current.__context__
        )
    return {
        "error_type": chain[0].split("@", 1)[0],
        "error_message": "operation failed",
        "error_stack": " <- ".join(chain),
    }


class LogRuntime(QueueListener):
    def __init__(
        self,
        settings: LogSettings,
        events: Mapping[str, str],
        routes: Mapping[tuple[str, str], str],
        sink: int | None,
        limit: int,
        state: str,
    ) -> None:
        self.settings = settings
        self._events = {
            **_BASE_EVENTS,
            **{
                key: value
                for key, value in events.items()
                if type(key) is str
                and len(key) <= 64
                and re.fullmatch(r"[a-z_]+(?:[.][a-z_]+)*", key)
                and type(value) is str
                and len(value) <= 128
            },
        }
        self._routes = dict(routes)
        self._sink = sink
        self._limit = limit
        self._queue: queue.Queue[logging.LogRecord] = queue.Queue(maxsize=2047)
        super().__init__(self._queue)
        self._queue_handler = QueueHandler(self._queue)
        self._lock = threading.Lock()
        self._deadline: float | None = None
        self._stats = LogStats(sink_state=state)
        self._identity = {
            "service": settings.service if settings.service == "boxie" else "unknown",
            "role": settings.role if settings.role in {"api", "orchestrator"} else "unknown",
            "release": settings.release
            if type(settings.release) is str
            and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", settings.release)
            else "unknown",
            "instance_id": settings.instance_id
            if type(settings.instance_id) is str
            and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", settings.instance_id)
            else str(uuid4()),
        }
        if sink is not None:
            self.start()

    @property
    def stats(self) -> LogStats:
        with self._lock:
            return replace(self._stats, closed=self._deadline is not None)

    def configuration_error(self) -> None:
        with self._lock:
            self._stats.configuration_errors += 1

    def emit(
        self,
        event: str,
        *,
        level: int = logging.INFO,
        logger: str = "boxie.observability.telemetry",
        error: BaseException | None = None,
        **fields: object,
    ) -> None:
        if level < self.settings.level:
            return
        # Reject foreign objects before looking them up or comparing their values.
        if type(event) is not str or len(event) > 64 or event not in self._events:
            with self._lock:
                self._stats.dropped_count += 1
            return
        record: dict[str, Any] = {
            "schema_version": 1,
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "level": _LEVELS.get(level, "ERROR"),
            "event": event,
            "message": self._events[event],
            **self._identity,
            "logger": _logger_category(logger),
            **current_context(),
            **validated(fields),
        }
        if error is not None:
            record.update(_exception(error))
        line = (json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
        with self._lock:
            if (
                len(line) > self._limit
                or self._deadline is not None
                or self._stats.sink_state != "enabled"
            ):
                self._stats.dropped_count += 1
            else:
                try:
                    self._queue_handler.enqueue(
                        logging.LogRecord("boxie", level, "", 0, line, (), None)
                    )
                    self._stats.queued_records += 1
                except queue.Full:
                    self._stats.dropped_count += 1

    def dequeue(self, block: bool) -> logging.LogRecord:
        """Let QueueListener own the daemon; Queue.shutdown wakes idle readers."""
        if self._deadline is not None and time.monotonic() >= self._deadline:
            self._finish()
        try:
            return self._queue.get(block)
        except queue.ShutDown:
            self._finish()
            assert self._sink is not None
            os.close(self._sink)
            # QueueListener handles Empty without treating it as a queued sentinel.
            raise queue.Empty from None

    def handle(self, record: logging.LogRecord) -> None:
        # Only the listener daemon writes. Never hold a Python stream/handler lock.
        assert self._sink is not None
        line = cast(bytes, record.msg)
        try:
            while line:
                written = os.write(self._sink, line)
                if written <= 0:
                    raise OSError("closed log sink")
                line = line[written:]
        except OSError:
            self._finish("broken")
        else:
            with self._lock:
                self._stats.queued_records = max(0, self._stats.queued_records - 1)

    def _finish(self, state: str = "closed") -> None:
        with self._lock:
            if self._stats.sink_state == "enabled":
                self._stats.sink_state = state
            self._stats.dropped_count += self._stats.queued_records
            self._stats.queued_records = 0
            self._queue.shutdown(immediate=True)

    def begin_close(self, timeout_seconds: float = 2) -> float:
        """Signal terminal drain without joining; return the one shutdown deadline."""
        timeout = min(2.0, max(0.0, timeout_seconds)) if math.isfinite(timeout_seconds) else 0.0
        with self._lock:
            if self._deadline is None:
                self._deadline = time.monotonic() + timeout
                self._queue.shutdown()
            deadline = self._deadline
        return deadline

    def close(self, timeout_seconds: float = 2) -> None:
        deadline = self.begin_close(timeout_seconds)
        # Async callers only initiate drain: off-loop owner/atexit does any join.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._thread is not None and threading.current_thread() is not self._thread:
                self._thread.join(max(0.0, deadline - time.monotonic()))
            self._finish()

    def stop(self) -> None:
        self.close()


class _Handler(logging.Handler):
    def __init__(self, runtime: LogRuntime) -> None:
        super().__init__()
        self.runtime = runtime

    def emit(self, record: logging.LogRecord) -> None:
        # HTTPX emits this fixed record after receiving response headers.
        args = record.args
        if (
            type(record.name) is str
            and record.name == "httpx"
            and record.levelno == logging.INFO
            and record.exc_info is None
            and type(record.msg) is str
            and record.msg == 'HTTP Request: %s %s "%s %d %s"'
            and type(args) is tuple
            and len(args) == 5
            and type(args[0]) is str
            and args[0] in ("GET", "HEAD")
            and type(args[1]) is URL
            and args[1].path in ("/healthz", "/readyz")
            and type(args[3]) is int
            and 200 <= args[3] < 300
        ):
            return
        event = "foreign_log"
        if type(record.msg) is str and len(record.msg) <= 256 and type(record.name) is str:
            event = self.runtime._routes.get((record.name, record.msg), event)
        error = record.exc_info[1] if record.exc_info else None
        self.runtime.emit(event, level=record.levelno, logger=record.name, error=error)
        # Deliberately never getMessage(), format(), formatException() or handleError().


_runtime: LogRuntime | None = None
_config_lock = threading.Lock()


def configure_logging(
    settings: LogSettings,
    *,
    events: Mapping[str, str] | None = None,
    routes: Mapping[tuple[str, str], str] | None = None,
    _bootstrap: bool = False,
) -> LogRuntime:
    global _runtime
    with _config_lock:
        if _runtime is not None:
            if _runtime.settings != settings:
                _runtime.configuration_error()
            return _runtime
        sink: int | None = None
        state = "unavailable"
        try:
            sink = os.dup(sys.stdout.fileno())
            os.set_inheritable(sink, False)
            state = "enabled"
        except OSError, ValueError, AttributeError:
            if sink is not None:
                os.close(sink)
                sink = None
        _runtime = runtime = LogRuntime(settings, events or {}, routes or {}, sink, 8192, state)
        handler = _Handler(runtime)
        root = logging.getLogger()
        root.handlers[:] = [handler]
        root.setLevel(settings.level)
        logging.captureWarnings(True)
        # Remove pre-existing dependency handlers, including Uvicorn's raw stderr.
        for value in logging.Logger.manager.loggerDict.values():
            if isinstance(value, logging.Logger):
                value.handlers.clear()
                value.propagate = True

        if _bootstrap:
            _install_hooks(runtime)
            atexit.register(runtime.close)
        return runtime


def bootstrap_logging(
    settings: LogSettings,
    *,
    events: Mapping[str, str] | None = None,
    routes: Mapping[tuple[str, str], str] | None = None,
) -> LogRuntime:
    """Configure process logging after CLI parsing, preserving standard streams."""
    return configure_logging(settings, events=events, routes=routes, _bootstrap=True)


def _install_hooks(runtime: LogRuntime) -> None:
    def exception_hook(
        _kind: type[BaseException], error: BaseException, _tb: TracebackType | None
    ) -> None:
        runtime.emit("process_failed", level=logging.ERROR, error=error)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        runtime.emit("thread_failed", level=logging.ERROR, error=args.exc_value)

    def unraisable_hook(args: Any) -> None:
        runtime.emit("unraisable_failed", level=logging.ERROR, error=args.exc_value)

    sys.excepthook = exception_hook
    threading.excepthook = thread_hook
    sys.unraisablehook = unraisable_hook


def install_asyncio_handler(runtime: LogRuntime) -> None:
    def exception_handler(_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        error = context.get("exception")
        runtime.emit(
            "async_failed",
            level=logging.ERROR,
            error=error if isinstance(error, BaseException) else None,
        )

    asyncio.get_running_loop().set_exception_handler(exception_handler)


def run_process(
    runtime: LogRuntime,
    run: Callable[[], None],
    *,
    close_metrics: Callable[[float], None] | None = None,
) -> None:
    try:
        run()
    except BaseException as error:
        if not isinstance(error, SystemExit) or error.code:
            runtime.emit("process_failed", level=logging.ERROR, error=error)
        if isinstance(error, (SystemExit, KeyboardInterrupt)):
            raise
        raise SystemExit(1) from None
    finally:
        # This owner runs after asyncio.run has returned. Start log drain before
        # stopping/joining the metrics listener, sharing one terminal deadline.
        deadline = runtime.begin_close()
        try:
            if close_metrics is not None:
                close_metrics(max(0.0, deadline - time.monotonic()))
        finally:
            runtime.close(max(0.0, deadline - time.monotonic()))


def log_runtime() -> LogRuntime:
    """Return the explicitly initialized process runtime for composition code."""
    if _runtime is None:
        raise RuntimeError("logging not configured")
    return _runtime


def configured_runtime() -> LogRuntime | None:
    """Inspect without initialization; safe for embeddable app factories."""
    return _runtime
