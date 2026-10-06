# cli-bench

[Workspace](../../README.md) · [Design](../../docs/superpowers/specs/2026-10-06-cli-bench-design.md)

An original benchmark harness under development. The current slice provides
strict suite parsing/validation for Rust callers and CLI help/version/logging.
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

Source-local model/suite/parser tests and `tests/cli.rs` cover the implemented
interface. Native macOS 27.0.1 arm64 checks are recorded in the task report; Linux
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
`InvalidSuite` classifications. Process statuses belong to the CLI adapter.

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
declarations are validated now; runtime enforcement belongs to the planned
executor. Full/smoke policies encode 3/20 and 1/2 checked warmups/samples per role
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
environment/globs, or interpret embedded tokens. Symlink containment and non-UTF-8
invocation paths need runtime checks when filesystem execution is added.

Current output is plain help/version text with no pager or prompt. Shared logging
uses stderr and is initialized only by the CLI. No timing or optimization gain is
claimed. The harness currently makes no standards-compliance certification; this
slice implements its configuration/adapter foundation and records platform gaps.
