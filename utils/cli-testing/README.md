# cli-testing

`cli-testing` connects a CLI invocation, its inputs, expected bytes, and independent
reference recipe in one named test case. Use TOML for straightforward cases,
`#[test_case]` for a case with Rust customization, and ordinary Rust tests for
multiple commands, signals, or custom pipes. The pilot packages are
[catr](../../buildins/catr/tests/cases.toml),
[cutr](../../buildins/cutr/tests/cases.toml), and
[grepr](../../buildins/grepr/tests/cases.toml).

Add `cli-testing.workspace = true` under the consuming package's
`[dev-dependencies]`. Ordinary tests use committed fixtures, never execute the
reference, and never update expectations.

## Register a catalog

Keep the package's native tests in `tests/cli.rs`. Add a separate Cargo target:

```toml
[[test]]
name = "catalog"
path = "tests/catalog.rs"
harness = false
```

Put this runner in `tests/catalog.rs`:

```rust,ignore
fn main() -> std::process::ExitCode {
    cli_testing::run_catalog(&cli_testing::context!())
}
```

Each automatic case becomes an individually discoverable `libtest-mimic` test.
TOML is loaded at runtime; editing a recipe does not require regenerating Rust.
Invalid catalogs produce a failing `catalog_is_valid` test. For example, from
the repository root:

```sh
cargo test -p cutr --test catalog -- --list
cargo test -p cutr --test catalog tsv_b8 -- --exact
cargo nextest run -p cutr --profile ci -E 'test(=tsv_b8) | test(=tsv_b1_8)'
```

`tsv_b8` belongs to `catalog`; `tsv_b1_8` belongs to the native `cli` target.

## Write a straightforward case

A catalog lives at `tests/cases.toml`. This complete example uses catr's existing
input and expected files:

```toml
schema = 1
binary = "catr"

[references.bsd-cat]
program = "/bin/cat"
platform = "macos"

[[cases]]
name = "fox_stdin"
args = ["-"]
stdin = { mode = "pipe", files = ["inputs/fox.txt"] }
origin = { reference = "bsd-cat" }
expect = { exit = 0, stdout_file = "expected/fox.txt.out", stderr = "" }
```

Arguments are passed directly, without a shell, in the package directory. Fixture
fields are relative to `tests/`; command arguments remain relative to the package.
For file operands, list their dependencies explicitly, for example
`args = ["tests/inputs/fox.txt"]` with `inputs = ["inputs/fox.txt"]`.
Include every input dependency of the reference invocation so recording can detect
staleness. Directories in `inputs` include their descendant files.

Choose `stdin = { mode = "pipe", text = "hello\n" }` for inline input, or a
nonempty ordered `files` array for raw concatenation without inserted separators.
Use `stdin = { mode = "file", path = "inputs/fox.txt" }` for regular-file stdin.
Omitted stdin is closed. Both stdout and stderr need exactly one inline string
or fixture path; exit codes are exact numbers, including intentional failures.
Bytes are compared without trimming, sorting, newline conversion, or decoding.

## Customize a named case in Rust

Mark the catalog entry `execution = "rust"`. The automatic runner excludes it,
but reference recording still includes its recipe. In `tests/cli.rs`:

```rust,ignore
use cli_testing::{Context, Result, test_case};

#[test_case("tsv_b1_8")]
fn tsv_b1_8(cx: &mut Context) -> Result<()> {
    // BSD cut emits raw bytes; cutr replaces incomplete UTF-8 sequences.
    cx.map_expected_stdout(|bytes| {
        String::from_utf8_lossy(bytes).into_owned().into_bytes()
    })?;
    Ok(())
}
```

The attribute resolves the consuming package's binary, reads the case and required
fixtures, calls the body, then executes the command and checks exit code, stdout,
and stderr. The wrapper is a normal zero-argument `#[test]` with the original
function name. It preserves `cfg` and `ignore` attributes. Synchronous functions
must take one named `&mut Context` parameter and return `()` or
`cli_testing::Result<()>`; async, unsafe, const, generic, `test`, and `should_panic`
combinations are rejected with compiler diagnostics.

Call `cx.run()?` for early execution and custom assertions on `std::process::Output`.
Repeated calls and final checks reuse that output. After an attempted execution,
arguments, environment, cwd, stdin, and expectations are frozen. Failed attempts
are not retried. A body error or panic fails the test and prevents subsequent
automatic execution. It does not undo an earlier explicit `run()`.

| Context method | Use |
| --- | --- |
| `args`, `env`, `current_dir`, `write_stdin` | Customize the invocation before running. `args` appends. |
| `fixtures` | Access package input and expected byte files. |
| `workspace` | Create an owned temporary cwd; subsequent calls reuse it. An existing workspace remains accessible after running. |
| `map_expected_stdout`, `expect_stderr`, `expect_exit` | Customize the contract before running. Actual output stays unchanged. |
| `run`, `check` | Capture once, or capture and assert the configured contract. |

Temporary resources created by `cx.workspace()` stay alive through execution and
final checks, then are cleaned up. Copy needed operands into that workspace before
running. A local temporary directory created only inside the customization body
would be dropped before automatic execution; use the context's workspace instead.
Unknown IDs, missing fixtures, and entries without `execution = "rust"` fail at
runtime with the catalog/case context. There is no separate Rust scenario registry.

## Keep involved scenarios in ordinary Rust

`context!()` returns a `TestContext`. Its `command()` provides an ordinary
`assert_cmd::Command`; `std_command()` provides a standard process command with the
same cwd and environment defaults. Standard commands leave stream wiring,
deadlines, signal handling, and child cleanup to the caller.

```rust,ignore
use assert_fs::prelude::*;

#[test]
fn creates_a_file() -> Result<(), Box<dyn std::error::Error>> {
    let workspace = cli_testing::context!().workspace()?;
    workspace.command().arg("new").assert().code(0).stdout("").stderr("");
    workspace.child("new").assert("");
    Ok(())
}
```

This example belongs to touchr. A workspace exposes `assert_fs::ChildPath` through
`child()`, its directory through `path()`, and raw fixture copying through
`copy_input("source.txt", "nested/destination.txt")`. Workspaces are independent;
copying does not mutate committed fixtures. `TestContext::case(name)` also exposes
the prepared case API to manual tests; call `check()` yourself when using it.

The existing `cargo_bin_cmd!()`, `fixtures!()`, `Fixtures`, and
`command(binary, package_dir)` APIs remain available. Read expected fixtures before
starting a manually orchestrated process.

## Schema 1 reference

Unknown fields, duplicate or invalid names, missing files, absolute fixture paths,
parent traversal, and symlink fixture paths are rejected. Names use ASCII letters,
digits, `_`, and `-`. `binary` must match the owning Cargo package. Catalogs must
contain at least one case. Input fixtures live under `inputs/`, expected fixtures
under `expected/`.

| Table | Fields |
| --- | --- |
| Root | `schema = 1`, `binary`, `cases`; optional `env` string map and `references` map. |
| `references.NAME` | Absolute `program`, exact Rust OS `platform` such as `macos` or `linux`; optional `env` string map and `version_args` argument array for version identity. |
| Case | `name`, `args`, `origin`, `expect`; optional `execution` (`toml` by default, or `rust`), `inputs`, `stdin`, `env`, `adapt`. |
| `stdin` | `mode = "pipe"` with either `text` (including empty) or nonempty `files`; or `mode = "file"` with `path`. |
| `expect` | `exit` in 0–255; one of `stdout` / `stdout_file`, and one of `stderr` / `stderr_file`. Empty streams must still be declared. |
| `origin` | Exactly one known `reference` or nonempty `manual` explanation. Reference recipes may override `args`, `exit`, `stderr` (inline text), and `env`. Overrides require `reason`, except an exit identical to the candidate's. |
| `adapt` | `expected_stdout = "utf8-lossy"` and nonempty `reason`. Applies only to expected stdout; requires a reference origin. No other named adaptations are supported. |

Reference status and stderr default to the candidate's declared contract.
For grepr's no-match behavior, declare `expect.exit = 0`, `origin.exit = 1`, and
explain the difference. For a manual expectation, record its independent
specification or worked example; reference overrides and adaptations are invalid.

The TOML UTF-8 adaptation in [cutr's catalog](../../buildins/cutr/tests/cases.toml)
and the Rust adaptation above both retain raw reference fixture bytes. Neither
transforms captured candidate output. Prefer an explicit Rust assertion for a
more involved contract instead of expanding TOML into a programming language.

## Defaults and reference workflow

Commands use the package cwd, `LC_ALL=C`, `TZ=UTC`, `NO_COLOR=1`, `TERM=dumb`, and
CLI logging disabled. Inherited `SANCTUARY_LOG_*` settings are removed first.
Catalog environment overrides apply in order: suite, case, then (for references
only) reference and origin. Other host environment variables are inherited.
Tests must not change the parent process's cwd or environment.

Catalog capture and `assert_cmd` commands have a 30-second child timeout. Catalog
capture preserves stdin transport and drains streams concurrently. Descendants
that retain pipes can still prevent capture from completing; `assert_cmd` also has
a blocked-stdin-writer limitation. Keep inputs small and use nextest's outer CI
timeout. Standard process commands have no automatic deadline. Workspaces isolate
files but are not host sandboxes. Catalogs and their reference commands are trusted
repository code. The legacy `Fixtures` API checks relative names but permits
symlinks; catalog fixture paths are stricter.

Use [cli-fixtures](../../tools/cli-fixtures/README.md) to list, explain, validate,
compare, and stage independent reference output. `apply` explicitly promotes a
reviewed recording after checking its inputs, catalog, executable identity, and
artifacts for staleness or conflicts. See the
[CLI testing skill](../../.agents/skills/cli-testing/SKILL.md) for repository policy.

## Architecture

The [catalog model](src/catalog.rs) is shared by tests and the reference tool.
The [runtime](src/context.rs) owns preparation, resources, process capture, and
assertions. The [attribute crate](../cli-testing-macros/README.md) only generates a
wrapper around that runtime. [libtest-mimic registration](src/harness.rs) gives each
automatic recipe its own test identity, while native Rust retains the normal test
harness. This keeps ownership and reference provenance visible without duplicating
parsers or introducing a scenario registry.
