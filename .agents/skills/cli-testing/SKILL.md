---
name: cli-testing
description: Use when adding, changing, or reviewing CLI tests, fixture expectations, or reference-output generators in this repository.
---

# CLI testing

## Choose the test boundary

- Test observable CLI behavior through the built executable in the owning package.
  Keep native E2E tests in `tests/cli.rs`; catalog packages also register
  `tests/catalog.rs` as `[[test]] name = "catalog", harness = false`.
- Use `tests/cases.toml` for straightforward invocations. Use `#[test_case]` for
  Rust customization of one catalog case. Keep multiple commands, signals,
  deadlines, and custom pipe orchestration in ordinary Rust tests. Do not grow
  TOML into a programming language or add a separate Rust scenario registry.
- For a CLI behavior bug, add a failing CLI regression before the fix. Cover
  relevant boundaries: empty input, missing final newline, invalid UTF-8, stdin
  and file operands, `--`, invalid arguments, multiple files and filesystem effects.
- Unit tests are optional. Add one only for distinct logic difficult to reach
  through the CLI. Retain direct library tests for public I/O and error contracts
  that subprocess tests cannot expose. Do not duplicate the E2E case matrix.

## Connect cases and fixtures

Add `cli-testing.workspace = true` under `[dev-dependencies]`. Keep reusable inputs
in `tests/inputs/` and raw expectations in `tests/expected/`. Read the
[helper guide and schema](../../../tools/cli-testing/docs/testing.md) when authoring a
catalog or using the context API. The representative catalogs are catr, cutr, and
grepr; existing packages can retain their native tests and generators.

A catalog case names its arguments, input dependencies, stdin transport, origin,
and exact exit code/stdout/stderr. For example, inside a catr catalog with the
`bsd-cat` reference declared:

```toml
[[cases]]
name = "fox_stdin"
args = ["-"]
stdin = { mode = "pipe", files = ["inputs/fox.txt"] }
origin = { reference = "bsd-cat" }
expect = { exit = 0, stdout_file = "expected/fox.txt.out", stderr = "" }
```

Fixture fields are relative to `tests/`; arguments run from the package root.
Declare file operands in `inputs`, including directory dependencies, so recording
can hash them. Pipe `files` concatenate in order without separators. Regular-file
stdin is a separate explicit mode. Every retained expected file needs a catalog
owner, including Rust-owned recipes; remove unused fixtures only after checking
native tests and reference generators too.

Compare exact bytes without trimming, sorting, newline conversion, or wildcard
matching. If the contract needs an adaptation, explain it beside the case.
`expected_stdout = "utf8-lossy"` is the only named TOML adaptation and changes
only expected bytes. Document command-specific differences in the package README.
Manual origins must name an independent specification or worked example.

## Customize a Rust-owned case

Set `execution = "rust"` in the catalog, then connect it directly in `tests/cli.rs`:

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

The wrapper loads fixtures, calls the body, runs the candidate, and checks both
streams and the exact exit code. `cx.run()?` permits early custom assertions;
subsequent calls and final checks reuse the output. Configuration freezes after
an execution attempt. A body error or panic prevents subsequent automatic launch.
Do not add `#[test]` or `#[should_panic]`; `cfg` and `ignore` remain supported.
Use `cx.workspace()` for temporary resources needed after the body returns.

## Write manual process scenarios

`context!()` provides reusable `TestContext` helpers. `command()` returns
assert_cmd; `std_command()` supplies the same environment/cwd defaults while
leaving pipe wiring, deadlines, signals, and child cleanup to the test.
`workspace()` owns an assert_fs temporary directory and supplies `child()`,
`copy_input()`, and commands rooted there. Assert filesystem effects explicitly.

```rust,ignore
use assert_fs::prelude::*;

#[test]
fn creates_file() -> Result<(), Box<dyn std::error::Error>> {
    let workspace = cli_testing::context!().workspace()?;
    workspace.command().arg("new").assert().code(0).stdout("").stderr("");
    workspace.child("new").assert("");
    Ok(())
}
```

This example belongs to touchr. Existing `cargo_bin_cmd!()`, `fixtures!()`, and
`assert_fs::TempDir` remain available. Read input and expected files before running
a manual command. Assert exact status and both streams: `.failure()` alone can
accept a crash. Use explicit predicates only for genuinely variable diagnostics.

Shared commands use package cwd, C locale, UTC, plain output, and disabled logging.
Other environment variables are inherited. Override relevant settings on the
child, never the test process. Committed inputs are read-only. Logging tests must
opt into a level and temporary destination.

Keep ordinary piped fixtures small and run nextest with `--profile ci`. Child
capture deadlines do not bound descendants retaining pipes; assert_cmd also has a
blocked-stdin-writer limitation. Standard commands need explicit deadlines and
cleanup. Workspaces do not sandbox host access. Use PTY/manual checks for
interactive behavior; help and startup smoke tests do not cover interaction.
Permission tests need an unprivileged user. Report platform requirements and skips.

## Record independent expectations

For catalog packages, use [cli-testing](../../../tools/cli-testing/README.md):

1. Verify `command -v cli-testing` and run `cli-testing --json doctor`. Use
   `discover --root DIR` to locate catalogs and pass the selected `--suite PATH`.
   Inspect `list` / `explain`, the package README, and the selected reference.
   Verify its implementation and version or OS release. Pilot BSD references are
   explicit macOS system paths; cutr requires `LC_ALL=en_US.UTF-8`. Never silently
   substitute GNU tools or generate expectations from the candidate.
2. Use `validate` for catalog and fixture ownership, then `compare --candidate`
   for automatic cases. Run Rust-owned cases through native tests; comparison
   reports them without executing their customization bodies.
3. `record --output DIR` stages raw reference streams and provenance in a new
   directory. It checks the reference's declared status and stderr, including
   intentional nonzero statuses, and records Rust-owned reference recipes too.
   Declare and explain candidate/reference differences in the catalog.
4. Review the completed manifest and every fixture change, including binary
   differences. Preview with `apply DIR --dry-run`; use `apply DIR` only for a reviewed capture within the authorized
   task. Stale, incomplete, or conflicting captures require a fresh recording.
   Do not auto-accept expectations in tests or CI.
5. Rerun owning tests. Keep manual expectations independently justified, and
   record executable/OS identity and generation commands in the change description.

For packages still using `mk-outputs.nu`, read that script and README first.
Follow its executable choices, arguments, cwd, and locale; there is no shared
legacy generator interface. Check reference status/stderr yourself, review all
bytes, and inspect partial changes after failures: legacy scripts may overwrite
fixtures before failing. Ordinary tests require no Nushell or reference tools.

For the evidence behind helper choices and byte/status handling, read
[research](references/research.md).

## Select verification

Run `cargo nextest run -p PACKAGE --profile ci` and affected consumers for shared
changes. Run `cargo test -p PACKAGE --doc` when the package has a library. For new
catalogs and attributes, verify individual Cargo/nextest discovery and filtering.
Run relevant formatting, lint, and test tools directly; leave full validation to
CI. Do not run prek manually.

Run `uv run cli-check -p PACKAGE` after structural changes. It checks registration,
including the catalog target, but semantic validation stays in Rust. Use
`cli-testing validate` for catalogs and unreferenced expected files. See the
[check reference](../../../tools/cli-check/docs/checks.md) and
[PR workflow](../../../.github/workflows/pull-request-check.yml). Report checks and
limitations; preserve passing results until relevant inputs change.
