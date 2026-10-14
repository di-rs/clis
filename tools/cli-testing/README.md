# CLI test policy

Run from the repository root:

```sh
uv run --locked python tools/cli-testing/cli_test_policy.py
```

An optional positional path selects another workspace root. The command checks
that each CLI package has a nonempty, enabled `tests/cli.rs` integration-test
target and a `cli-testing` development dependency. It rejects the old
`mk-outs.nu` generator name and requires
`tests/expected/` when a package has `mk-outputs.nu`.

With `autotests = false`, an explicit `[[test]]` must register `tests/cli.rs`:
either `name = "cli"` with its default path, or any name with
`path = "tests/cli.rs"`. A target with `test = false` does not count. An explicit
`cli` target pointing elsewhere suppresses automatic discovery, so another
enabled target must register the entrypoint. These checks inspect manifest
registration only; they do not evaluate feature conditions or test contents.

This is a structural check. It does not parse Rust or prove test completeness,
assertion quality, helper usage, or reference correctness. Review and execution of
the tests establish those properties. Libraries need no CLI tests, and packages
without generated fixtures need no fixture directories.

Discovery expands the root Cargo workspace's `members` globs and `exclude`
patterns, including a root package when present. It recognizes explicit `[[bin]]`
targets and automatic `src/main.rs` and `src/bin/` targets, respecting `autobins`.
It does not resolve implicit workspace members introduced only by path
dependencies; keep repository packages covered by the root `members` list.
Cargo remains responsible for validating manifests and the complete target graph.

The `cli-test-policy` prek hook and the existing Python CI job run this command.
Regression tests create temporary workspace trees and invoke the command with
the same Python interpreter as pytest. The tool needs no runtime dependencies or
Cargo installation.
