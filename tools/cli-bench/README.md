# cli-bench

[Workspace](../../README.md) · [Design](../../docs/superpowers/specs/2026-10-06-cli-bench-design.md)

An original benchmark harness under development. The current slice provides
strict suite parsing/validation, immutable evidence/artifact storage for Rust
callers, bounded child execution, role/profile invocation resolution, and CLI
help/version/logging, isolated Cargo revision builds, executable binding, and
verified Biggie dataset preparation.
Full run/check execution remains planned in the linked design.

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

Source-local model/suite/store/artifact/host/process/invocation/build tests, real child lifecycle
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

Resource defaults are 128 cases, 256 MiB per captured stream, 32 MiB per generated
file, 128 MiB generated inputs per run, 2 GiB evidence, 120 seconds per sample,
and 1,800 seconds per build.
Limits must be positive; deadline overrides require an explicit reason. Suite
limits accept explicit overrides. `ProcessRunner` enforces explicit per-command
deadlines and captured/drained stream limits. Dataset preparation enforces declared
input budgets before spawning and monitors actual output sizes; total-evidence
runtime enforcement belongs to later orchestration. Full/smoke policies encode 3/20 and 1/2 checked warmups/samples per role
in each of two batches, with separate 5/1 RSS samples. Smoke policy suppresses
performance conclusions.

Dataset assertions accept only `byte-pattern`, `text-shape`, and `records`.
Validation computes their exact byte counts with checked arithmetic and rejects
overflow or conflicting sizes within a recipe, including profile replacements.
Stream and filesystem assertions remain case-only. Preparation checks every selected
profile recipe against the file limit and their checked sum against the run limit
before starting any generator. An unselected larger profile does not consume that
execution budget.

Generator argv checks explicit deterministic seed/pattern/schedule forms and one
output token. Biggie's complete argument validation is deferred to execution;
`bind_roles` resolves explicit binaries and the generator; `prepare_datasets`
executes the bound generator's recipes. Comparator execution remains separate. Named correctness targets are modeled now; their availability and actual
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

Current output is plain help/version text and build JSON, with no pager or prompt. Shared logging
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
identity; no current compiler is attributed to it. Build records can be supplied by library callers; `build_revision` produces them
from verified isolated builds. Hashes
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
when correctness checks assert an exact directory tree. `DatasetSet` wraps stable IDs and `InputRecord` paths/content identities, plus
`GenerationRecord` recipe/generator/shape evidence. Tiny literal test fixtures can
omit generation records without inventing benchmark provenance.

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

## Building committed revisions

From the repository root, `build -p/--package NAME -r/--revision REF` builds a
committed package and writes an immutable `ArtifactRecord` as JSON to stdout.
`-t/--toolchain` selects an already installed Rust toolchain; the default resolves
the active installed toolchain once. `-F/--features` accepts comma-separated
features, `-N/--no-default-features` disables defaults, and `-d/--data-dir` defaults
to `.cli-bench`. The package must have exactly one binary; multi-binary packages
require a suite or a Rust `BuildRequest` naming the exact binary. There is no
standalone binary-selection flag. Tool discovery uses the adapter's PATH;
unavailable tools or toolchains fail without installation or fallback.

```sh
cargo run --locked -p cli-bench -- build --help
# Example for a committed utility; compilation is outside measurement:
cargo run --locked -p cli-bench -- build -p tailr -r HEAD
```

`HEAD` selects committed source. Dirty HEAD builds explicitly warn that uncommitted
edits are excluded and suggest prebuilt mode. Ref resolution passes literal argv
with Git's `--end-of-options`; subsequent work uses only the resolved full SHA.
Builds use owned detached local snapshots outside the active repository and fresh
target directories under `target/cli-bench`. They never switch/stash/reset the
active checkout. Submodules, LFS pointers, escaped source symlinks and external
local path dependencies are rejected. Snapshot cleanup is best effort and touches
only the newly owned directory; build captures and targets remain disposable cache
files. This is source isolation for cooperative builds, not a hostile-code sandbox.

Cargo uses release/locked builds and each commit's own `Cargo.lock`. Differing
lockfiles are recorded revision provenance. Compiler/Cargo version output and
executable hashes are checked before and after builds. Git optional index writes
are disabled. Only PATH/HOME/TMPDIR/locale/timezone values from `GitContext` reach
Git; global/system Git config and prompting are disabled. Cargo receives explicit
`HOME`/`CARGO_HOME` (owned defaults when omitted), the selected RUSTC, requested flags,
strip policy and target directory. Ambient compiler wrappers are disabled.

The selected executable comes from Cargo JSON, with exact workspace package/binary
matching and containment checks. Provenance retains a redacted compiler-artifact
projection (target, profile, features, freshness), package/version dependency
records, configuration hashes and a hash of the effective build environment. Source
URLs and configuration/environment contents are not published. Raw build logs under
`target/cli-bench` are local diagnostics and may contain tool output; they are not
copied into immutable artifact records. Lockfile, configuration and source edits
during compilation fail verification. Cache keys cover commit, lockfile, selected
toolchain identities, target, requested features/flags/strip, configuration,
dependencies, environment and package/binary; reuse verifies retained provenance
and executable contents. There is no cache eviction.
The environment hash is also part of `ResolvedBuildPolicy`, so tagged experiments
reject its drift. It hashes the effective explicit child map after policy overrides;
the selected compiler path and owned target/default `HOME`/`CARGO_HOME` paths become
stable markers. Supplied environment values remain hashed, including explicit
user-home/cache/PATH settings. Legacy or caller-supplied records may have no hash;
verified builds always supply one matching their Cargo evidence.

`resolve_revision(repository, reference, &git_context, &runner)`,
`build_revision(&request, &store, &runner)` and
`bind_roles(&request, &suite, &store, &runner)` are public Rust operations. All
execution paths are absolute UTF-8; callers supply an existing scratch directory
outside the repository, Git identity, frozen Cargo/rustc identities and child
settings. These APIs use the supplied runner's deadline/cancellation/stream limits.
`BuildRequest.binary = None` uses sole-binary selection; `Some(name)` is exact.
`RunRequest` uses typed revision/prebuilt choices and optional build tools so
all-prebuilt bindings need no compiler. `bind_roles` retains candidate, selected
comparators and `RoleBindings.generator`; invocation-only callers may leave that
generator field absent. Prebuilt provenance stays unknown, and a requested missing
reference fails. No GNU/BSD substitution is performed.

`run` and `check` currently validate only `-s/--suite`, candidate `-r/--candidate-ref`
or `-a/--candidate`, previous `-b/--previous-ref` or `-p/--previous`, explicit
`-x/--reference`, and generator `-G/--generator-ref` or `-g/--biggie` selections.
They require a candidate plus comparator and reject ref/path conflicts. Valid
selections return operational failure explaining that execution is not implemented;
they do not claim to produce benchmark evidence. Other planned selection flags,
CLI preparation wiring, correctness orchestration and measurements remain later work.

Tests use tiny temporary Git/Cargo repositories and fake Cargo fault cases; they
generate no benchmark data. Native macOS execution is verified in the task report;
Linux remains unverified. No performance claim is made for build orchestration.

## Verified dataset preparation

`prepare_datasets(&suite, &DatasetPreparation { profile, bindings, expected },
&store, &runner)` uses the explicit generator in `RoleBindings`, separately from
all measured roles. The caller supplies isolated HOME/config directories and
allowlisted child settings. Suite generator settings and actual bound build/content
identity are retained separately; an explicit generator override may use a different
revision. Only that bound executable produces benchmark bytes.

The operation validates the complete suite, selects profile replacements, computes
exact sizes independently, and checks file and aggregate budgets before any child
starts. It expands exactly one whole `@output` argument into a fresh staging path.
`ProcessRunner::execute_with_file_limit` monitors that regular file in its existing
5 ms process loop and checks again after cleanup, bounded by declared size, file cap
and remaining run budget. This is a fallback with possible write overshoot, not an
OS hard quota. Ordinary `execute` keeps its existing timing and cleanup policy.
Generation timeout/cancellation/capture policy comes from the supplied runner.

Successful output must match every streamed shape check and, when consumed by
`@records`, safe LF path decoding. SHA-256, byte count, exact expanded argv,
recipe/profile, generator artifact/build identity and diagnostic hashes are retained.
Recipe identities include effective allowlisted settings; physical isolated
HOME/config locations are execution resources, not portable recipe identity.
Generation diagnostics and failure records remain in pending/failed dataset entries.
Failure records preserve expected and observed hashes when available. Errors return
before any caller can receive a complete prepared set.

Only independently verified output is atomically published under
`datasets/<recipe-hash>/`; existing entries are never overwritten or repaired.
Reuse rechecks input bytes, shapes, provenance and stdout/stderr diagnostics.
`verify_datasets(&set)` rechecks input identities and attached generation shapes
without changing expected values. Literal fixtures may omit generation evidence.
These operations stream file bytes with 8 KiB buffers; path expansion retains
memory proportional to the resulting argument list. Preparation is outside timing.

For replay, pass `expected: Some(&original_set)`. Saved metadata must be valid and
its dataset set, effective recipe/profile/generator identities and output hashes
must match. Old physical input files need not exist when regenerating in another
store. Changed seeded output fails and retains mismatch evidence; the original
set and cache metadata are unchanged. Ordinary preparation uses `expected: None`.

Ordinary integration tests use tiny original literal fault fixtures, never benchmark
generation. Native Biggie generation is explicitly opt-in:

```sh
cargo build --locked -p biggie
CLI_BENCH_BIGGIE="$PWD/target/debug/biggie" cargo test --locked -p cli-bench \
  --test library native_biggie -- --ignored
```

This case creates only 29 bytes across three tiny recipes and checks reuse. It is
correctness evidence for the supplied native binary, with unknown prebuilt build
provenance; it is not a performance sample or a pinned-revision compatibility claim.
Native macOS execution was checked for this slice; Linux remains unverified.

## Correctness gate and reusable reset

`prepare_experiment(&ExperimentPreparation, &Store, &ProcessRunner)` accepts one
explicit suite, profile, validated `CaseId` selection, role/build `RunRequest` and
optional immutable replay datasets. An empty selection includes every declared
case; duplicate or unknown IDs fail. Preparation binds executables, generates and
verifies datasets, and allocates unique marked scratch under `RunRequest.cache_root`
(typically `target/cli-bench`). Retained inputs and evidence stay in the Store.

`validate_experiment(prepared, &mut writer, &runner)` runs every selected case and
role, retaining `validation/report.json` with every observed status, stopped
outcome, byte identity, directory mode and check result. The writer must belong to
the same suite. Failure returns no `ValidatedExperiment`. Exact byte comparisons
preserve whitespace, NUL and invalid UTF-8. Independent literals, shapes and tail
slices catch shared comparator bugs. Expected nonzero direct exits remain values.
Missing named comparators fail. The gate captures output, then repeats each
invocation with its declared sink to verify status, stderr, observable file output
and declared directory effects. These extra validation invocations are untimed.

A successful capability exposes prepared roles, cases, profile, datasets, scratch
and report through read-only accessors. `revalidate()` rechecks retained input and
executable/tool identities without refreshing expectations. Later sample callers
must call it at finalization and use `verify_effects(case, role)` after invocations.
The latter checks declared exact directory types/paths and relevant permission bits
against that role's successful gate. V1 directory rules intentionally exclude
creation timestamps; file, symlink and content effects remain unsupported.

`create_scratch(cache_root, case_id)` requires an existing absolute directory with
no symlink components, creates a fresh marked owner and separate workload root,
and returns the only public reset capability. `reset_case(case, datasets, scratch)`
verifies input identities, ownership and the entire existing tree before deleting
entries, restores the original workload-root permissions, and recreates/verifies
the exact initial directory set (including required ancestors). It supports both
absent and existing-directory mkdir scenarios during preflight and future samples.
Markers stay outside the workload tree. No automatic scratch cleanup or cache
eviction is performed. This is cooperative containment, not a race-proof sandbox.

Children inherit the same process configuration; the harness never changes umask.
The report retains the existing host umask observation, including explicit
unavailability on macOS, and compares actual requested mode bits. It does not infer
umask from permissions or claim that an unavailable value was verified. Native
macOS regression tests cover this library workflow; Linux remains unverified.
Timing, RSS, host measurement locks and CLI run/check wiring remain later work.
No performance claim is made for preparation, verification or reset.
