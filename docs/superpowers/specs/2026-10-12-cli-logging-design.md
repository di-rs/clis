# Unified CLI logging

All 20 workspace binaries need persistent, inspectable errors and command activity
for a future shell dashboard. Use the standard `log` facade with the maintained
`flexi_logger` backend. Distributed traces and timing spans are unnecessary for
this requirement. Libraries emit events; binaries initialize logging.

`cli-log` is a shared configuration and command-boundary adapter. It selects system
log directories, records start/finish/error/panic events, and preserves existing
terminal diagnostics and exit statuses. It delegates serialization, file writing,
rotation, cleanup, and console output to the backend. Do not implement a custom
schema, writer, lock protocol, truncation policy, or interrupted-write recovery.
Shared `run`/`try_run` helpers handle initialization, parsing, and completion;
command-specific diagnostics and status mappings remain in each binary.

Use native JSON Lines and one file family per CLI invocation. This avoids shared
rotation coordination between processes. The startup event holds the optional
shell session ID; filenames identify the CLI and invocation for subsequent
records. Rotation retains three 5 MiB backups per invocation. Cleanup across old
invocations is a future shell responsibility, not a global bound promised here.

Default file level is info; console logging is off. `CLIS_LOG_DIR`,
`CLIS_LOG_LEVEL`, `CLIS_LOG_STDERR`, and `CLIS_LOG_SESSION` configure the adapter.
Biggie's existing level flag selects the file threshold, Grepr's verbosity selects
console output, and Kara keeps console logging disabled. Explicit errors and
lifecycle events persist at every file threshold except off. Intentional nonzero
exit status is recorded as completion, not automatically classified as an error.

Preserve help, data output, rich reports, and partial-success behavior. Explicitly
flush application buffers so output failures return a failing status; Echor
reports these with status 1 instead of panicking. Setup failures warn without
replacing the command's status. Messages can include application-supplied data;
there is no automatic content capture or redaction.
New Unix log directories use `0700`; existing permissions remain unchanged.

The [utility README](../../../utils/cli-log/README.md) documents integration,
configuration, native records, and operational limits. Tests cover this adapter's
contract and representative CLI errors, not reimplementations of backend internals.

References: [log](https://docs.rs/log/latest/log/),
[flexi_logger](https://docs.rs/flexi_logger/latest/flexi_logger/),
[XDG state directories](https://specifications.freedesktop.org/basedir/0.8/).
