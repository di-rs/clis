# Read CLI log files

## Format and retention

Each invocation gets its own files, avoiding concurrent processes rotating the
same file. An active filename is
`<command>_<pid>-<startup-nanoseconds>_rCURRENT.jsonl`; rotated files use numbered
suffixes. Use command and invocation from the filename to group all its records.
The backend rotates at 5 MiB and retains three rotated files **per invocation**.
Operators must manage retention across invocations; this utility does not
impose a directory-wide size or age limit.

Use `flexi_logger`'s native JSON Lines format: `timestamp`, uppercase `level`,
`text`, optional source/thread details, and structured values under `kv`.
For example, a completion record contains:

```json
{"level":"INFO","timestamp":"2026-10-12T14:00:00Z","text":"command finished","kv":{"event":"command_finished","exit_code":0,"elapsed_ms":12}}
```

The `command_started` event's `kv` includes `command`, `pid`, `invocation_id`,
and optional `session_id`. Other boundary events are `command_finished`, `error`,
`argument_error`, and `panic`. Ordinary library events have no required event
field. Completion records contain numeric `exit_code` and `elapsed_ms`; an
intentional status such as `false` returning 1 is not an error. Lifecycle and
explicit boundary errors bypass the ordinary severity threshold, except `off`,
which disables all persistence. Panic reporting chains the previous hook.

The shell can pass a directory and session identifier, discover invocation files,
and tail complete newline-terminated records. Read numbered files for earlier
records after rotation. A kill or uncaught panic can leave a start without a
finish; reconcile this with child process status. No dashboard, collector, custom
schema, or database is included here.

Writes use the backend's direct mode, without an asynchronous queue. New Unix log
directories are private (`0700`); existing directory permissions stay unchanged.
File permissions follow the process umask. Logging is not an fsync-backed audit trail.
No argv, environment dump, stdin, or file contents are automatically collected.
Application and parser errors can contain paths or rejected values; messages are
not automatically redacted. Grepr logs match activity without matched contents.
