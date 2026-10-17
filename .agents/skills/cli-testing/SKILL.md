---
name: cli-testing
description: Use when adding, changing, or reviewing CLI tests, fixture expectations, or reference-output generators in this repository.
---

# CLI testing

## Choose the test boundary

- Test observable CLI behavior through the built executable in the owning
  package's `tests/cli.rs`. E2E coverage is required.
- For a CLI behavior bug, add a failing CLI regression before the fix. Cover
  relevant boundaries: empty input, missing final newline, invalid UTF-8, stdin
  and file operands, `--`, invalid arguments, multiple files and filesystem effects.
- Unit tests are optional. Add one only for distinct logic difficult to reach
  through the CLI. Retain direct library tests for public I/O and error contracts
  that subprocess tests cannot expose. Do not duplicate the E2E case matrix or
  add tests merely to raise counts.

## Build commands and read fixtures

Add `cli-testing.workspace = true` under `[dev-dependencies]`. Use
`cli_testing::cargo_bin_cmd!()` in the consuming package's integration tests;
its result is an ordinary `assert_cmd::Command`.

Keep reusable inputs in `tests/inputs/` and expected bytes in `tests/expected/`.
`fixtures!().read_input(name)` and `read_expected(name)` return bytes and fail
for missing files. Read expected files before running the command. Short argument
and status cases can stay inline; packages without fixtures need no empty folders.

```rust,ignore
use cli_testing::{cargo_bin_cmd, fixtures};

#[test]
fn copies_file_bytes() -> std::io::Result<()> {
    let expected = fixtures!().read_expected("message.out")?;
    cargo_bin_cmd!()
        .arg("tests/inputs/message.txt")
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}
```

For a pipe case, read input bytes first and pass them to `.write_stdin(input)`.
Assert both streams and the exact status. `.failure()` alone can accept a crash
as an intended error. Use explicit predicates for variable diagnostics while
retaining checks on the other stream and status.

Compare raw bytes without trimming, lossy decoding, sorting, newline conversion
or wildcard matching. If the specified behavior needs a narrow transformation,
document it at that assertion. Keep command-specific differences in the owning
package's tests and README. See the [helper API](../../../utils/cli-testing/README.md).

## Isolate process state

Shared commands use the package cwd, C locale and UTC, request plain output,
and disable logging. Other environment variables are inherited. Configure relevant
settings on the child; never change the test process's environment or cwd.

Use a fresh `assert_fs::TempDir` for file mutations and logs, then assert the
filesystem effects explicitly. Committed inputs are read-only. Logging tests
must opt into the desired level with a temporary destination.

Keep ordinary piped fixtures small and run nextest with `--profile ci`.
assert_cmd's timeout does not bound descendants or a blocked stdin writer.
Redirecting a regular file to stdin exercises a different mode from a pipe.
Use PTY/manual checks for interactive behavior; help and startup smoke tests do
not cover interaction. Permission tests need an unprivileged user. State platform
requirements and report skips or untested terminal behavior.

## Regenerate independent expectations

1. Read the package's `mk-outputs.nu` and README before running it. These are
   direct package scripts: follow their executable choices, arguments, cwd and
   locale requirements. Do not assume a shared generator interface.
2. Verify the selected upstream implementation and version, or the OS release
   for a BSD tool without a version flag. Record that identity and the generation
   command in the change description. GNU and BSD tools are not interchangeable.
3. Generate only the compatible cases with the independent reference. Capture
   binary output faithfully and check expected status and stderr, including
   intentional nonzero statuses. Never generate expectations from the candidate.
4. Review every changed or newly created fixture, including byte differences,
   before accepting it. Plain scripts may overwrite files before a later failure;
   inspect partial changes after errors. Do not assume staging or rollback.
5. Rerun the owning CLI tests. If the reference and candidate intentionally
   differ, document a narrowly justified assertion adaptation or a manually
   derived expectation. When no reference exists, use a behavior specification
   or independently worked example. Never auto-accept snapshots in tests or CI.

Ordinary tests use committed fixtures and need no Nushell or reference program.
Keep implementation-specific caveats beside the affected CLI. For the evidence
behind library choices and byte/status handling, read [research](references/research.md).

## Select verification

Start with `cargo nextest run --locked -p PACKAGE --profile ci`; run
`cargo test --locked -p PACKAGE --doc` only when that package has a library.
Include affected consumers for shared changes. The [verification map](../../../docs/testing.md)
links formatting, lint, platform and tooling checks and their configuration.

Run `uv run --locked cli-check` after structural test changes. Its
[documented checks](../../../tools/cli-check/README.md) verify package boundaries,
test registration, documentation and benchmark layout, not assertion quality or
reference correctness. Select `cli-check tests` to inspect only test conventions.
Report checks and limitations; preserve passing results until relevant inputs change.
