"""Bounded producers and a sole-owner, atomic pipe writer.

Only bootstrap_logging takes over fd1/fd2. The launcher must exclusively transfer
its write description (close all unused aliases). Direct terminals/files/sockets
are unsupported: delivery disables without refusing application startup.
"""

import asyncio
import atexit
import logging
import math
import os
import queue
import re
import stat
import sys
import threading
import time
from collections.abc import Callable, Mapping, MutableMapping
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast
from uuid import uuid4

import structlog

from service_logging.context import current_context, validated

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
    "request_completed": "Request completed",
    "request_failed": "Request failed",
}
_LEVELS = {10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR", 50: "CRITICAL"}
_DROP_REASONS = ("queue_full", "disabled", "broken_sink", "oversize", "shutdown", "invalid_event")


@dataclass(frozen=True)
class LogSettings:
    service: str
    role: str
    release: str = "unknown"
    instance_id: str = ""  # A generated process UUID; not hostname/environment content.
    level: int = logging.INFO


@dataclass(frozen=True)
class LogStats:
    queued_records: int
    dropped_count: int
    drops: tuple[tuple[str, int], ...]
    delivered_count: int
    configuration_errors: int
    sink_state: str
    max_event_bytes: int
    closed: bool


def _logger_category(name: object) -> str:
    if type(name) is not str or len(name) > 128:
        return "dependency"
    for prefix in ("boxie", "playground", "uvicorn", "temporalio", "service_logging"):
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


class LogRuntime:
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
                and re.fullmatch(r"[a-z_]{1,64}", key)
                and type(value) is str
                and len(value) <= 128
            },
        }
        self._routes = dict(routes)
        self._sink = sink
        self._limit = limit
        self._state = state
        self._queue: queue.Queue[bytes] = queue.Queue(maxsize=2047)
        self._lock = threading.Lock()
        self._wake = threading.Event()
        self._deadline: float | None = None
        self._pending = 0
        self._drops = dict.fromkeys(_DROP_REASONS, 0)
        self._delivered = 0
        self._configuration_errors = 0
        self._identity = {
            "service": settings.service
            if settings.service in {"boxie", "cli-playground"}
            else "unknown",
            "role": settings.role
            if settings.role in {"api", "agent", "worker", "gateway"}
            else "unknown",
            "release": settings.release
            if type(settings.release) is str
            and re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", settings.release)
            else "unknown",
            "instance_id": settings.instance_id
            if type(settings.instance_id) is str
            and re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", settings.instance_id)
            else str(uuid4()),
        }
        self._renderer = structlog.processors.JSONRenderer(
            ensure_ascii=False, separators=(",", ":")
        )
        self._thread: threading.Thread | None = None
        if sink is not None:
            self._thread = threading.Thread(target=self._write, name="json-log-writer", daemon=True)
            self._thread.start()

    @property
    def stats(self) -> LogStats:
        with self._lock:
            return LogStats(
                self._queue.qsize() + self._pending,
                sum(self._drops.values()),
                tuple(self._drops.items()),
                self._delivered,
                self._configuration_errors,
                self._state,
                self._limit,
                self._deadline is not None,
            )

    def configuration_error(self) -> None:
        with self._lock:
            self._configuration_errors += 1

    def emit(
        self,
        event: str,
        *,
        level: int = logging.INFO,
        logger: str = "service_logging",
        error: BaseException | None = None,
        **fields: object,
    ) -> None:
        if level < self.settings.level:
            return
        # Reject foreign objects before looking them up or comparing their values.
        if type(event) is not str or len(event) > 64 or event not in self._events:
            with self._lock:
                self._drops["invalid_event"] += 1
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
        line = (cast(str, self._renderer(None, "", record)) + "\n").encode("utf-8")
        if len(line) > self._limit:
            record["truncated"] = True
            # IDs are omitted intact; never truncate their identity.
            for key in (
                "error_stack",
                "workflow_id",
                "request_id",
                "sandbox_id",
                "operation_id",
                "run_id",
                "generation",
                "activity_attempt",
                "duration_ms",
                "reason",
                "outcome",
                "error_message",
                "error_type",
            ):
                record.pop(key, None)
                line = (cast(str, self._renderer(None, "", record)) + "\n").encode("utf-8")
                if len(line) <= self._limit:
                    break
            while len(line) > self._limit and len(record["message"]) > 1:
                record["message"] = record["message"][: max(1, len(record["message"]) // 2)]
                line = (cast(str, self._renderer(None, "", record)) + "\n").encode("utf-8")
        with self._lock:
            if len(line) > self._limit:
                self._drops["oversize"] += 1
            elif self._deadline is not None:
                self._drops["shutdown"] += 1
            elif self._state != "enabled":
                self._drops["disabled"] += 1
            else:
                try:
                    self._queue.put_nowait(line)
                except queue.Full:
                    self._drops["queue_full"] += 1
                self._wake.set()

    def _write(self) -> None:
        sink = self._sink
        assert sink is not None
        pending: bytes | None = None
        try:
            while True:
                with self._lock:
                    if self._deadline is not None and time.monotonic() >= self._deadline:
                        break
                    if pending is None:
                        try:
                            pending = self._queue.get_nowait()
                            self._pending = 1
                        except queue.Empty:
                            if self._deadline is not None:
                                break
                    self._wake.clear()
                if pending is None:
                    self._wake.wait(0.01)
                    continue
                try:
                    written = os.write(sink, pending)
                    if written != len(pending):
                        raise OSError("atomic pipe contract violated")
                except BlockingIOError:
                    self._wake.wait(0.01)
                    continue
                except OSError:
                    with self._lock:
                        self._state = "broken"
                    break
                with self._lock:
                    self._delivered += 1
                    self._pending = 0
                pending = None
        finally:
            os.close(sink)
            with self._lock:
                reason = "broken_sink" if self._state == "broken" else "shutdown"
                self._drops[reason] += self._pending
                self._pending = 0
                while True:
                    try:
                        self._queue.get_nowait()
                        self._drops[reason] += 1
                    except queue.Empty:
                        break
                if self._state == "enabled":
                    self._state = "closed"

    def close(self, timeout_seconds: float = 2) -> None:
        timeout = min(2.0, max(0.0, timeout_seconds)) if math.isfinite(timeout_seconds) else 0.0
        with self._lock:
            if self._deadline is None:
                self._deadline = time.monotonic() + timeout
            deadline = self._deadline
            self._wake.set()
        # Async callers only initiate drain: off-loop owner/atexit does any join.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            if self._thread is not None and threading.current_thread() is not self._thread:
                self._thread.join(max(0.0, deadline - time.monotonic()))


class _Handler(logging.Handler):
    def __init__(self, runtime: LogRuntime) -> None:
        super().__init__()
        self.runtime = runtime

    def emit(self, record: logging.LogRecord) -> None:
        event = "foreign_log"
        if type(record.msg) is str and len(record.msg) <= 256 and type(record.name) is str:
            event = self.runtime._routes.get((record.name, record.msg), event)
        error = record.exc_info[1] if record.exc_info else None
        self.runtime.emit(event, level=record.levelno, logger=record.name, error=error)
        # Deliberately never getMessage(), format(), formatException() or handleError().

    def flush(self) -> None:
        pass


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
        limit, state = 8192, "not_bootstrapped"
        if _bootstrap:
            state = "unsupported"
            try:
                if stat.S_ISFIFO(os.fstat(1).st_mode):
                    limit = min(8192, os.fpathconf(1, "PC_PIPE_BUF"))
                    sink = os.dup(1)
                    os.set_inheritable(sink, False)
                    os.set_blocking(sink, False)
                    state = "enabled"
            except OSError:
                if sink is not None:
                    os.close(sink)
                    sink = None
                state = "unavailable"
            # Never flush old streams: buffered secrets and exit flushes go to null.
            null = os.open(os.devnull, os.O_WRONLY)
            try:
                os.dup2(null, 1)
                os.dup2(null, 2)
            finally:
                if null not in (1, 2):
                    os.close(null)
        _runtime = runtime = LogRuntime(settings, events or {}, routes or {}, sink, limit, state)
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

        def processor(_logger: object, method: str, event_dict: MutableMapping[str, Any]) -> Any:
            event = event_dict.pop("event", "")
            error = event_dict.pop("exc_info", None)
            runtime.emit(
                event,
                level={
                    "debug": 10,
                    "info": 20,
                    "warning": 30,
                    "error": 40,
                    "critical": 50,
                    "exception": 40,
                }.get(method, 20),
                error=sys.exception()
                if error is True
                else error
                if isinstance(error, BaseException)
                else None,
                **cast(dict[str, Any], validated(event_dict)),
            )
            raise structlog.DropEvent

        structlog.configure(
            processors=[processor],
            logger_factory=structlog.ReturnLoggerFactory(),
            cache_logger_on_first_use=False,
        )
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
    """Explicit earliest process boundary. Never call from libraries/app factories."""
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


def run_process(runtime: LogRuntime, run: Callable[[], None]) -> None:
    try:
        run()
    except BaseException as error:
        if not isinstance(error, SystemExit) or error.code:
            runtime.emit("process_failed", level=logging.ERROR, error=error)
        if isinstance(error, (SystemExit, KeyboardInterrupt)):
            raise
        raise SystemExit(1) from None
    finally:
        runtime.close()


def log_runtime() -> LogRuntime:
    """Return the explicitly initialized process runtime for composition code."""
    if _runtime is None:
        raise RuntimeError("logging not configured")
    return _runtime


def configured_runtime() -> LogRuntime | None:
    """Inspect without initialization; safe for embeddable app factories."""
    return _runtime
