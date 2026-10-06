# cli-bench

[Workspace](../../README.md) · [Design](../../docs/superpowers/specs/2026-10-06-cli-bench-design.md)

An original benchmark harness under development. The current slice provides
strict suite parsing/validation, immutable evidence/artifact storage for Rust
callers, bounded child execution, role/profile invocation resolution, and CLI
help/version/logging.
Benchmark execution commands are planned in the linked design.

## Quick start

Run from the workspace root:

```sh
cargo run --locked -p cli-bench -- --help
cargo run --locked -p cli-bench -- --version
```

## CLI contract

`-h/--help` and `-V/--version` write to stdout and return zero. No arguments shows
help. `-L/--log-level` accepts `off`, `error`, `warn`, `info`, `debug`, `trace`;
explicit flags override `CLIS_LOG_LEVEL`, whose default is `off`. Diagnostics use
stderr. Invalid arguments or effective logging values return 2. Help and version
are handled before logging initialization. There is no interactive prompt, pager,
color policy or benchmark output in this slice.

## Evidence and limits

Source-local model/suite/store/artifact/host/process/invocation tests, real child lifecycle
tests in `tests/process.rs`, `tests/cli.rs`, and the public
consumer lifecycle in `tests/library.rs` cover the implemented interface. Native macOS 27.0.1 arm64 checks are recorded in the task report; Linux
has not been run. No performance measurement or GNU/BSD compatibility claim is
made for this custom harness. Full benchmark execution remains pending.

## Rust library

A consumer beside the workspace can declare:

```toml
[dependencies]
cli-bench = { path = "../clis/tools/cli-bench" }
```

`parse_suite` accepts UTF-8 TOML and returns a validated `Suite`.
`validate_suite` also checks models constructed or changed by direct Rust callers:

```rust
use cli_bench::{BenchError, parse_suite, validate_suite};

let source = include_str!(concat!(env!("CARGO_MANIFEST_DIR"),
    "/tests/inputs/minimal-suite.toml"));
let suite = parse_suite(source)?;
validate_suite(&suite)?;
# Ok::<(), BenchError>(())
```

Both functions perform no I/O, launch no tools, and initialize no process state.
They retain memory proportional to the supplied configuration. Callers own suite
text and models; validation leaves direct-call models unchanged. `BenchError`
retains TOML parse sources and exposes `kind()` with `UnsupportedSchema` and
`InvalidSuite` classifications. Evidence operations additionally expose `Evidence`
and `Io`; process failures expose `Execution`. I/O and JSON errors retain their
sources. Native process statuses are returned as data; CLI exit mapping belongs
to the adapter.

The [approved v1 contract](../../docs/superpowers/specs/2026-10-06-cli-bench-suite-v1.md)
defines every model field and default. Public model types represent roles,
full/smoke policies, resource limits, build/generator identity, profile overrides,
I/O boundaries, correctness rules, mutation setup and meaningful work. Unknown
fields/variants, duplicate IDs, NULs, escaping paths, invalid counts/statuses,
unsupported versions and missing deterministic Biggie recipes are rejected.
Arguments remain strings: only whole tokens are recognized; `@@` escapes a
literal leading `@`. Partial child environment tables inherit safe defaults.

Resource defaults are 128 cases, 256 MiB per captured stream, 8 GiB generated
inputs, 2 GiB evidence, 120 seconds per sample, and 1,800 seconds per build.
Limits must be positive; deadline overrides require an explicit reason. These
declarations are validated now. `ProcessRunner` enforces explicit per-command
deadlines and captured/drained stream limits; generated-input and total-evidence
runtime enforcement belongs to later orchestration. Full/smoke policies encode 3/20 and 1/2 checked warmups/samples per role
in each of two batches, with separate 5/1 RSS samples. Smoke policy suppresses
performance conclusions.

Generator argv checks explicit deterministic seed/pattern/schedule forms and one
output token. Biggie's complete argument validation is deferred to execution;
no generator or selected comparator is discovered or invoked by this library
slice. Named correctness targets are modeled now; their availability and actual
assertions are checked when execution bindings are implemented. Directory effects
currently model directories only; file/symlink/content effects require an extension.

## Text and presentation

Suite text and serialized paths/argv are UTF-8. Literal NUL expectations use hex;
UTF-8 strings containing NUL are rejected. Relative scratch/output paths reject
absolute paths, dot/parent components, repeated/trailing separators, backslashes
and Windows drive prefixes. Parsing does not normalize argument text, expand
environment/globs, or interpret embedded tokens. Invocation resolution rejects non-UTF-8 paths and existing symlinks beneath
scratch paths; caller-owned execution roots must be real directories. This is
containment validation for cooperative workloads, not a hostile-filesystem sandbox.

Current output is plain help/version text with no pager or prompt. Shared logging
uses stderr and is initialized only by the CLI. No timing or optimization gain is
claimed. The harness currently makes no standards-compliance certification; this
slice implements its configuration/adapter foundation and records platform gaps.


## Evidence library

`Store::open(path)` creates or opens a marked evidence root. Use `.cli-bench/`
for local evidence; only that root path is ignored. Cargo caches remain separate.
Nonempty unmarked roots and evidence symlinks are rejected. `begin_run(&suite)`
allocates a never-reused directory with an OS-backed ownership lock and incomplete
status. `begin_run_source(text)` additionally preserves the exact submitted TOML;
the model-only API stores an equivalent serialization. Dropping a writer leaves
its incomplete evidence. No operation changes the process cwd or environment.

`fingerprint(path)` streams SHA-256 with an 8 KiB buffer and records logical size.
`register_binary(path, build, &store)` copies and checks bytes and provenance before
publishing an immutable artifact. `None` build provenance means unknown prebuilt
identity; no current compiler is attributed to it. Build records are explicit
caller-supplied provenance: the build stage must resolve and verify them. Hashes
identify executable contents, not dynamic dependencies or the kernel.

`RunWriter::append_event` durably appends typed events. `finish` checks manifest
bindings, required input identities, tool identities and all retained artifacts,
then flushes evidence and its checksum inventory before publishing terminal status.
I/O failure retains an incomplete status; no empty successful result is substituted.
`record_failure` retains failed/incomplete outcomes with unresolved identities absent.
`Store::load_run` verifies finalized evidence for offline consumers without executing
it. The outcome record is a storage foundation; timing/RSS/analysis records and
actual benchmark execution remain pending.

`begin_tagged_run` immediately records an attempt under `experiments/ID/` with
its hypothesis, change summary and requested revisions. A resolved experiment
requires a known Git-built previous role. The first resolved attempt fixes the
starting SHA and the `MeasurementContract` identity over the complete suite,
full/smoke profile, harness/generator/engine identities, validator/analysis versions
and resolved build policy. Candidate identity, selected declared cases and physical
paths remain run bindings. Actual input identities are retained in each manifest
and frozen on first observation per declared dataset; subsequently observed
confirmation datasets can extend that registry without changing the suite. Policy,
starting SHA or known input drift fails and keeps the attempt reference.

`collect_host` performs bounded native OS/CPU/RAM probes, exposes unavailable fields
explicitly, and copies only allowlisted/redacted child settings supplied by the
caller. It never enumerates the environment or mutates umask. Store transaction and
per-run locks protect evidence operations; the host-wide measurement-session lock
belongs to the future execution layer. Do not infer host idleness from these locks.

Storage operations take time proportional to copied/verified evidence. No performance
claim is made for this foundation. Native Linux execution is still unverified.

## Process execution library

`ProcessRunner::new(ExecutionPolicy)` accepts a deadline, a positive per-stream
byte cap and a caller-owned `Arc<AtomicBool>` cancellation token. `execute` takes
a `CommandSpec` and fresh `CapturePaths`. Paths must be absolute UTF-8 and argv
must be NUL-free. The runner clears the inherited child environment and applies
only the supplied map; it never changes global cwd/environment or installs signal
handlers. Callers supply isolated HOME/config directories where needed.

Each call creates an owned Unix process group, checks cancellation/deadlines while
concurrent readers drain stdout and stderr with 8 KiB buffers, and reaps the direct
child. Cleanup sends SIGTERM then SIGKILL after a one-second grace when necessary,
with at most one additional second of nonblocking termination confirmation. This
controls cooperative descendants that remain in their group; it is not an OS
sandbox. On macOS, transient EPERM during exit is reconciled by reaping and checking
group absence within these bounds. Only verified absence permits ignoring EPERM;
persistent permission failures and other errors remain failures.

After cleanup, all capture readers stop through 5 ms readiness polling, flush and
close their files, and are joined before return. Persistent cleanup failure never
claims confirmed termination. A still-live direct child is handed to a private
waiter holding only its child handle, with a requested 128 KiB stack, until eventual
exit/reaping; that wait can last indefinitely if the OS prevents termination.
Waiter creation failure is reported alongside the primary error, without blocking
the caller. No capture writer is detached, and partial capture evidence remains stable.
An unconfirmed live process may still affect its own scratch files.

`CommandInput` supports null or a regular file. `CommandOutput` selects captured
bytes, a drained pipe, discard, or a fresh file sink. Stderr is always captured
separately. Captures stop at the declared byte cap and never collect complete
streams in RAM. File/discard sinks preserve those actual descriptors and do not
report captured stdout bytes; total file/evidence quotas belong to orchestration.
Output files use exclusive creation and are never overwritten. A failed run keeps
partial capture evidence. Capture storage must be an ordinary responsive filesystem.

`ProcessOutcome` distinguishes `Exit(code)` and `Signal(number)` and retains
`Timeout`, `Cancelled` or `OutputLimit(stream)` separately. Call `check_expected`
to require the exact declared exit code: actual zero does not satisfy expected one,
and no stopped run or signal satisfies an expected exit. Cancellation before spawn
returns an execution error without opening outputs. Repeated calls use fresh paths.

```no_run
use cli_bench::{CapturePaths, CommandInput, CommandOutput, CommandSpec,
    ExecutionPolicy, ProcessRunner};
use std::{collections::BTreeMap, sync::{Arc, atomic::AtomicBool}, time::Duration};

let runner = ProcessRunner::new(ExecutionPolicy {
    timeout: Duration::from_secs(120),
    max_stream_bytes: 268_435_456,
    cancellation: Arc::new(AtomicBool::new(false)),
});
let command = CommandSpec {
    program: "/bin/echo".into(),
    argv: vec!["literal $(text)".into()],
    cwd: "/tmp".into(),
    environment: BTreeMap::from([("LC_ALL".into(), "C".into())]),
    stdin: CommandInput::Null,
    stdout: CommandOutput::Capture,
};
let result = runner.execute(&command, &CapturePaths {
    stdout: "/tmp/cli-bench-example.stdout".into(),
    stderr: "/tmp/cli-bench-example.stderr".into(),
})?;
result.check_expected(0)?;
# Ok::<(), cli_bench::BenchError>(())
```

These process tests require only native POSIX shell/system utilities, not Python,
reference installations, Hyperfine or benchmark datasets. Native macOS execution
is covered; Linux execution remains unverified.

## Invocation resolution library

`resolve_invocation(&case, role, profile, &bindings, &datasets, scratch)` resolves
one explicit role and measurement profile per call. `RoleBindings` contains the
complete role map of `BoundExecutable` paths/artifact identities, allowlisted child
settings, existing isolated HOME/config directories and optional identified
`PipelineTools`. Config directories should be outside the workload scratch root
when correctness checks assert an exact directory tree. `DatasetSet` wraps stable
IDs and `InputRecord` paths/content identities; generation and shape/provenance
records belong to the later preparation stage.

Resolution validates the case, checks bound content identities, applies profile
replacement and role fallback, and expands only whole tokens. `@input:ID` produces
one path, `@scratch:REL` a contained path, `@records:ID` one contained argument per
UTF-8 LF-terminated path record, and `@@` escapes a leading `@`. Record decoding
rejects blank, CR/NUL, invalid UTF-8 and escaping paths. Existing scratch symlinks
are rejected. No substring, glob, environment or shell expansion is performed.
The resolver creates no files, executes no tools and keeps memory proportional
to the expanded argv. Callers keep these bound resources stable until execution.

Regular-file stdin preserves a seekable file descriptor. Pipe stdin uses a fixed
Bash script with `pipefail`: an explicitly bound cat reads the finite input, and
all paths/argv are passed as positional parameters. Its `InvocationScope::Pipeline`
includes shell and producer overhead; direct commands have `Direct` scope. Both
producer and consumer must succeed. Nonzero expected pipe status and declared
early-exit pipe policies are rejected by suite validation. Correctness callers may
select `CommandOutput::Capture` while retaining the same stdin/argv boundary.
The ordinary fixtures probe FIFO versus regular-file descriptors and producer
failure without requiring benchmark tools.
