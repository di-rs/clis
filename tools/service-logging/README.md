# Bounded service logging

Boxie and CLI Playground declare this small Python dependency independently.
Each service owns its templates and assembles its HTTP middleware and process.
A small neutral ASGI helper owns the common request context/timing/emission. The
shared producer owns validated context, JSON rendering, bounded transport and
sanitized process error hooks. The [event schema](../../infra/observability/event.schema.json)
is the cross-service contract. The neutral `metrics` module supplies bounded
HTTP/log instruments and the owned private listener; importing logging does not
create instruments or start exporters. Domain instruments and route catalogs
remain in Boxie and Playground. The historical package name is retained.

`bootstrap_logging(LogSettings(...))` is an explicit process-root operation.
It duplicates an owned stdout pipe into a descriptor above 2, keeping it separate
from takeover even if stdin or stderr is closed. It makes the duplicate
non-inheritable and nonblocking, and redirects fd 1 and fd 2 to `/dev/null` without flushing. The
launcher must close all unused write-end aliases: `dup` shares file-status flags.
Only the writer owns/closes the sink. Children needing output must use explicit
pipes or PTYs; they must never inherit the sink. App factories and imports never
take over streams. `configure_logging` alone installs the producer with disabled
delivery; it does not assume ownership of its caller's streams.

Records are rendered before enqueueing; the writer never sees a LogRecord,
exception, context variable or arbitrary object. INFO is the default threshold.
A queue of 2047 records plus one pending writer record retains at most 2048
complete lines. Each complete UTF-8 line, including its newline, is at most
`min(8192, PC_PIPE_BUF)` bytes: 512 on the tested macOS pipe and 4096 on Linux.
Oversized records are re-rendered with `truncated=true`, dropping optional IDs
intact and reducing the trusted message if needed. Impossible required envelopes
drop with `oversize`; JSON bytes are never sliced. Producer serialization has
fixed field, chain and string bounds; this is not a total-process RSS guarantee.

Only registered trusted message templates, finite categories and validated
primitives cross the producer. Foreign stdlib/Uvicorn/Core diagnostics retain a
fixed message, severity and logger category. Their message, arguments, fields,
URLs, exception messages, notes, source and locals are discarded. Exception
chains retain at most four fixed class categories and four module-category/line
frame identifiers per exception. Correlations accept canonical UUIDs and the
exact `boxie/sandbox/<UUID>` workflow identity; consumer IDs are currently omitted.
Request IDs are generated locally, never copied from request headers. Release
metadata accepts a 40- or 64-character lowercase hex digest from `BOXIE_RELEASE`
or `PLAYGROUND_RELEASE`, otherwise `unknown`; instances receive a process UUID.

Repeated same-settings configuration returns the same runtime. Conflicting live
settings increment `configuration_errors` without replacing it. Close is terminal
until process restart. `close(timeout_seconds=2)` establishes one monotonic budget
of at most two seconds, reused by explicit close and atexit. It only joins outside
an active asyncio loop; async callers initiate drain and process ownership handles
the eventual join. `run_process` signals log drain before closing the optional
owned metrics listener, passing only the time remaining on that same deadline
to its off-loop join. Metrics listeners also retain their first close deadline.
Handler flush/close do not drain or perform sink I/O. A blocked
pipe drops newest diagnostics and cannot make this writer block process exit.
The budget allows scheduler overhead; it is not a hard real-time promise.

Unsupported terminal/file/socket stdout or a missing/broken sink disables delivery
without refusing application startup. Immutable `runtime.stats` snapshots expose
queue depth, fixed drop reasons, delivery count, configuration errors and sink
health for the next observability layer. Raw Python/native stdout/stderr and
unintegrated fatal diagnostics are discarded after bootstrap. Exit-code/health
monitoring remains necessary, especially if both logging and metrics are absent.
The tested local container topology is not proof of production journal delivery,
native gVisor execution, collector configuration or activation readiness.

Run the focused subprocess suite from the root with:

```sh
UV_CACHE_DIR=/tmp/boxie-uv-cache uv run --locked pytest tools/boxie/tests/test_observability_logging.py tools/cli-playground/tests/test_observability.py
```

The explicit `integration` case uses disposable production PostgreSQL/Temporal
fixtures, real authenticated HTTP/WebSocket traffic and workflow replay, with a
substituted Docker port. It checks actual producer stdout and decoded persisted
history payload bytes. The ordinary suite separately verifies HTTP and OS PTY
progress while its log pipe remains unread.
