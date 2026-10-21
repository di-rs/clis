# cli-testing

`cli-testing` helps Rust CLI tests start the package's binary and compare its
output with byte fixtures. Add `cli-testing.workspace = true` to the consuming
package's `[dev-dependencies]`, then put integration tests under `tests/`.

## Write a fixture test

Read expected output before starting the command so a missing fixture fails the
test directly:

```rust,ignore
use cli_testing::{cargo_bin_cmd, fixtures};

#[test]
fn copies_stdin() -> std::io::Result<()> {
    let fixtures = fixtures!();
    let input = fixtures.read_input("message.txt")?;
    let expected = fixtures.read_expected("message.out")?;

    cargo_bin_cmd!()
        .arg("-")
        .write_stdin(input)
        .assert()
        .code(0)
        .stdout(expected)
        .stderr("");
    Ok(())
}
```

Place the example's files at `tests/inputs/message.txt` and
`tests/expected/message.out`. `fixtures!()` resolves paths from the consuming
package; it never decodes, trims, or updates their bytes.

## Command defaults

`cargo_bin_cmd!()` returns an ordinary `assert_cmd::Command` for the package's
binary. It sets the package working directory, C locale, UTC, plain terminal
output, disabled CLI logging, and a 30-second timeout. Override child settings
through the command builder. Use `command(binary, package_dir)` for an explicit
executable path.

Other environment variables are inherited, and no filesystem sandbox is created.
The timeout does not cover descendants or a blocked stdin writer; keep nextest's
outer timeout. Fixtures are trusted repository files, so relative-name validation
does not prevent symlinks from pointing elsewhere.

See the [CLI testing skill](../../.agents/skills/cli-testing/SKILL.md) for independent
reference fixtures, status assertions, and verification commands.

## Architecture

The [command helpers](src/lib.rs) configure `assert_cmd` at the process boundary.
The [fixture helper](src/fixtures.rs) resolves paths and reads bytes without
interpreting expected output. Call-site macros supply the consuming package's
manifest directory and binary name, so tests retain ownership of their fixtures
and assertions.
