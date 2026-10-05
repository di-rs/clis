# Library-first Rust architecture

[Project overview](../README.md) · [Contributing](../CONTRIBUTING.md)

This guide explains the boundaries required by [U1 and U8](north-star.md#every-utility).
It is a migration target, not a claim that every package already follows it.
Every command's useful domain operation must become
available to Rust consumers without invoking a subprocess or initializing a CLI.
This includes custom apps; Kara's terminal frontend is separate from reusable
editing operations. Do not refactor Kara as a side effect of unrelated utility work.

## Responsibility boundaries

```text
CLI caller -> main / cli adapter -> typed options -> library operation
Rust caller --------------------> typed options -> library operation
                                                    |
                                           focused shared mechanisms
```

The dependency direction is inward. Domain libraries do not depend on a CLI
adapter. CLI spelling and reference option precedence are normalized before execution;
semantic invariants must also be enforced for direct library callers.

A growing utility normally uses this layout:

```text
<utility>/
  Cargo.toml
  README.md
  src/
    main.rs          # process setup, streams, diagnostics, exit status
    cli.rs           # Clap parsing and conversion to domain options
    lib.rs           # documented public API and re-exports
    options.rs       # domain configuration, when it merits its own module
    error.rs         # typed errors, when more than io::Error is needed
    <operation>.rs   # focused implementation modules, when needed
  tests/             # follow CONTRIBUTING's shared test conventions
  benches/           # CLI benchmark recipe and/or registered library benchmarks
```

This describes responsibilities, not mandatory empty files. Small utilities can
keep domain types and operations in `lib.rs`; a trivial command may not need a
`cli.rs`. Do not move all packages or add separate core/CLI crates merely to
standardize directory names. Split packages only for a demonstrated dependency,
release, or consumer boundary.

## Public API contract

Use domain names and types, not parser artifacts: options express what to do,
not whether the caller spelled an option `-n` or `--number`. Prefer enums to invalid
combinations of booleans. `TryFrom<Args>` is useful at the adapter; validated
constructors or operation-level validation protect Rust callers too.

Accept explicit resources. Byte-stream operations normally take `Read`/`BufRead`
and `Write`; file operations take `Path`/`PathBuf` and explicit policy. Treat `-`
as stdin at the CLI boundary, not as a magical filesystem path in the core API.
Use `OsString` for CLI operands that need not be UTF-8. Do not replace invalid data
with replacement characters in a byte-oriented command.

Libraries may perform I/O when that is their domain, but it must be explicit.
Do not parse process argv, read global standard streams, change cwd/environment,
install a logger or panic hook, or call `process::exit` from domain operations.
Resolve process environment and defaults at the adapter and pass the needed
values. Document relative-path interpretation; never use `chdir` to satisfy an API.
Do not introduce a mock-filesystem trait unless a real test or implementation needs it.

Return results that let callers decide policy. Use `io::Error` when sufficient,
or a small typed error enum with context and source errors. A grep-like no-match
outcome is data, not necessarily an I/O error. For multi-file operations, represent
partial failure so the CLI cannot print an error and then accidentally report
success. Keep exit-code mapping and human-facing diagnostics at the process edge.
Avoid a workspace-wide catch-all error type that obscures utility-specific meaning.

Streaming APIs may emit to a caller-owned writer or sink; they need not materialize
all output as a collection. Return useful counts/status where appropriate. Specify
whether a call flushes, preserves state across inputs, or leaves a stream advanced.
The CLI must flush buffers it owns and handle failures before returning success.
A Rust caller should be able to use the API twice in one process without hidden
initialization, global state changes, or unexpected output.

## Readability and performance

Keep functions focused and names aligned with the command's semantics. Comment
invariants, compatibility decisions, and non-obvious optimizations rather than
restating each statement. Document errors, side effects, and important memory/time
bounds on public APIs. Use private modules and a small public surface by default.

Prefer streaming and buffer reuse. Avoid cloning whole inputs, converting bytes
to strings unnecessarily, or collecting a traversal before processing it. Bound
retained state where the algorithm permits; for tail-by-lines, account for long
records rather than claiming a fixed-byte bound from a fixed line count.
A seek-based optimization needs a correct non-seekable alternative where stdin
is supported. State that distinction in the API rather than requiring `Seek`
from every input just because the first implementation used a file.

Do not add parallelism, memory mapping, SIMD, caches, or `unsafe` by default.
Require a measured bottleneck, a correctness argument, platform/fallback tests,
and benchmark evidence. Ordering, memory growth, cancellation, and error delivery
must remain defined. Use cancellation/time abstractions for genuinely long-running
operations when required, not as mandatory infrastructure for every utility.

## Sharing across utilities

Share mechanisms whose contracts really match: for example, a byte-record reader,
a tested numeric parser, or a filesystem primitive. Keep utility-specific rules,
defaults, option interactions, and exit policies local unless they are explicitly
modeled and tested. The same flag spelling is not proof of shared semantics.

Before adding a shared crate under `utils/`, identify its actual callers, smallest
API, dependency cost, and tests. Prefer extracting from two concrete consumers;
a smaller justified extraction is acceptable, but speculation about future projects
is not enough. Shared crates must not depend back on utility crates or implicitly install
process-wide state. CLI support is an adapter capability: domains depend only on
the tracing facade, not on configuration, collectors, or diagnostic sinks. Follow
[observability](observability.md) for typed errors, anyhow at the CLI boundary, and
subscriber ownership. Test affected consumers whenever a shared contract changes.

Inherit existing workspace dependency versions and lints rather than independently
pinning the same dependencies in each package. Workspace declarations do not take
effect in a member without explicit inheritance; see the
[Cargo workspace reference](https://doc.rust-lang.org/cargo/reference/workspaces.html).
Do not broaden features or add heavyweight dependencies just to make interfaces look
uniform. Use `[lints] workspace = true` where applicable; avoid blanket lint
suppression, unchecked failure paths, and unjustified `unsafe`. If library consumers need to avoid CLI-only dependencies, introduce and
test an optional CLI feature or separate adapter crate in that scoped change;
no such feature is assumed to exist today.

## Migration and acceptance

Start with one utility and its real consumers. Characterize current behavior;
move useful logic behind a typed API; have the CLI call it; add direct API tests;
then extract shared pieces when another utility demonstrates the same need.
Do not change unrelated defaults as part of moving code. Explain public API changes
and retain compatibility wrappers when practical for existing callers.

A migrated operation is acceptable when its CLI and direct-library tests exercise
the same implementation, errors and output are explicit, and a documented consumer
example compiles. Test invalid direct options, injected I/O failures, repeated calls,
and byte/path boundaries where relevant. Check performance after structural changes,
not just feature additions.

Current starting points include [catr's reader/writer API](../coreutils/catr/src/lib.rs)
and [tailr's stream/seek APIs](../coreutils/tailr/src/lib.rs). They illustrate useful
boundaries, not complete compatibility or a requirement to preserve every existing
name. The consumer example below uses the current `catr` API.

## Using a utility as a library

The target for every app is a reusable operation behind the CLI, not a wrapper
that launches a subprocess. Existing APIs vary; inspect the package's `src/lib.rs`
and README before depending on it. For example, `catr` already accepts explicit
readers, writers, and options:

```rust
fn main() -> std::io::Result<()> {
    let options = catr::Flags {
        number_lines: true,
        number_nonblank_lines: false,
        squeeze_blank: false,
    };
    let mut output = Vec::new();
    catr::write_lines(&b"alpha\nbeta\n"[..], &mut output, &options)?;
    assert_eq!(output, b"     1\talpha\n     2\tbeta\n");
    Ok(())
}
```

This example belongs in a consumer crate with a dependency on the local `catr`
package. For a consumer beside this checkout, its `Cargo.toml` can use:

```toml
[dependencies]
catr = { path = "../clis/coreutils/catr" }
```

The example demonstrates the existing UTF-8-oriented API, not full `cat` parity.
Use the boundaries above when evolving this API for byte handling and full parity.

## Design references

[The Rust CLI book](https://rust-cli.github.io/book/tutorial/testing.html) demonstrates
separating testable logic from the command entry point.
[Rust API Guidelines](https://rust-lang.github.io/api-guidelines/) guide naming,
error types, documentation, and predictable public interfaces.
[Ripgrep's core description](https://github.com/BurntSushi/ripgrep/blob/master/crates/core/README.md)
separates CLI glue from reusable matcher/searcher/printer crates; its core itself
is not a reusable public library. Borrow that boundary, not its entire crate graph.
[Uutils](https://uutils.org/coreutils/docs/CONTRIBUTING.html) demonstrates per-utility
crates and shared helpers; a CLI-style entry point alone is not the library API
required here.
