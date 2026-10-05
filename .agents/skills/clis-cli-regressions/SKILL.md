---
name: clis-cli-regressions
description: Use when adding or updating CLI integration tests or reference-output fixtures for supported behavior in the clis workspace, including stdin, byte boundaries, and filesystem errors.
---

# CLI regression coverage

Read `AGENTS.md`, the app README, source, and existing `tests/cli.rs` to identify
supported behavior. Distinguish preserving current behavior from an authorized
behavior change. The [north star](../../../docs/north-star.md) targets GNU parity;
a scoped test task does not authorize implementing the entire missing surface.
In a tests-and-documentation task, report discovered production
defects without silently changing production behavior or blessing a defect as
intended behavior.

Build a small case matrix for the changed behavior: arguments, input, exit
status, stdout, stderr, and filesystem effects. Include relevant flags/conflicts,
errors, empty input, stdin, and boundaries such as Unicode, invalid UTF-8, or an
unterminated final record. Select cases that could expose the actual regression;
avoid enumerating every flag combination or duplicating existing coverage.

Use the app's `tests/cli.rs`, `assert_cmd::cargo::cargo_bin_cmd!`, `predicates`,
and small local helpers. For `bool`, select `cargo_bin_cmd!("true")` or
`cargo_bin_cmd!("false")`. For byte behavior, write byte input and compare raw
output bytes without decoding the captured output:

```rust
let mut command = cargo_bin_cmd!();
let assertion = command
    .args(["-b", "1-2", "-"])
    .write_stdin("éx\n".as_bytes().to_vec())
    .assert()
    .success()
    .stderr("");
assert_eq!(assertion.get_output().stdout.as_slice(), b"\xc3\xa9\n");
```

This illustrates `cutr` byte selection; adapt expectations to the documented
contract.
Do not convert actual output with `from_utf8_lossy` before asserting bytes.

Use `assert_fs` or existing `tempfile` helpers for writable fixtures. Set
permissions on temporary files; control child cwd/environment without changing
process-global state. Use fixed timestamps/dates. Permission-denied cases need
an environment that cannot read mode-000 files; report that limitation if running
with elevated privileges rather than treating it as a production failure.

Inline small cases; put reusable input/output in `tests/inputs` and
`tests/expected`. Follow the [reference-capture workflow](../../../docs/compatibility.md#reference-and-regression-workflow)
for GNU and adopted BSD additions; record provenance, statuses, and differences.
Normal tests must
not invoke reference commands or require Nushell. Do not add or run benchmarks
in a tests-and-documentation-only task.

For an authorized behavior fix, run the targeted test before the fix and confirm
it fails for the intended reason; apply the scoped fix, then run package checks
and the workspace suite from [CONTRIBUTING checks](../../../CONTRIBUTING.md#checks). For coverage of existing
behavior, verify the new cases pass and assert the intended contract, including
status and stderr. Use `catr/tests/cli.rs` for output/stdin patterns,
`pwdr/tests/cli.rs` for environment isolation, and `lsr/tests/cli.rs` for temporary
permission fixtures; these paths are under `coreutils/`.
