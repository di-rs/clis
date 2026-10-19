# cli-testing

Command setup and fixture loading for Rust CLI integration tests. Add
`cli-testing.workspace = true` under the consuming package's `[dev-dependencies]`.

Read fixtures first, then use the ordinary `assert_cmd` assertion chain:

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

| API | Purpose |
| --- | --- |
| `cargo_bin_cmd!()` | The consuming package's built binary as an `assert_cmd::Command`. An explicit binary name is also accepted. |
| `command(binary, package_dir)` | The same defaults for an explicit executable path. |
| `fixtures!()` | A `Fixtures` reader rooted at the consuming package's `tests/`. |
| `read_input(name)` / `read_expected(name)` | Fallible byte reads under `inputs/` / `expected/`; names must be relative. |

Command defaults: package cwd, `LC_ALL=C`, `TZ=UTC`, `NO_COLOR=1`, `TERM=dumb`,
disabled `SANCTUARY_LOG_*` logging and a 30-second `assert_cmd` timeout. Override child
settings through its builder. Other environment variables are inherited; no
filesystem sandbox is created. The timeout does not bound descendants or a
blocked stdin writer; retain nextest's outer CI timeout.

Fixture loading performs no decoding, trimming or updates. Reads use the fixture
root independently of the child cwd; errors include the failed path. These are
trusted repository files, so name validation does not restrict symlink targets.
The example belongs in a CLI package's integration test, where Cargo exposes its
binary path.

See the [testing skill](../../.agents/skills/cli-testing/SKILL.md) for test-writing
rules and the [verification map](../../docs/testing.md) for commands.
