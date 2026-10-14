# CLI testing research

Reviewed 2026-10-13. These sources inform this repository's
[CLI testing skill](../SKILL.md); they do not establish a universal test-layer ratio.

## Observable contracts

The Rust CLI book demonstrates integration tests that execute a built binary
and use known file contents. It treats unit and external tests as complementary:
argument parsing, startup, streams and files participate in observable behavior.
The repository's required E2E coverage and optional units are its own policy.
[Rust CLI book](https://rust-cli.github.io/book/tutorial/testing.html)

GNU coreutils maintainer guidance emphasizes per-test directories, cleanup,
deadlines, portability and exact return codes. Accepting any nonzero status can
hide a crash; shell builtins can also substitute for the intended executable.
[GNU testing guidance](https://www.pixelbeat.org/docs/coreutils-testing.html)

uutils shares command execution and fixture setup, uses `LC_ALL=C` and `TZ=UTC`,
and retains stdout/stderr as bytes. This supports sharing mechanics while keeping
command-specific expectations visible in their own tests.
[uutils test utilities](https://github.com/uutils/coreutils/blob/main/tests/uutests/src/lib/util.rs)

Rust's process result stores stdout and stderr as `Vec<u8>` and exposes status
separately. Byte comparison preserves distinctions involving NUL, invalid UTF-8,
CRLF and missing final newlines that text transformations can hide.
[Rust Output](https://doc.rust-lang.org/std/process/struct.Output.html)

## Library choices

| Library | Useful capabilities | Repository choice |
| --- | --- | --- |
| [assert_cmd](https://docs.rs/assert_cmd/latest/assert_cmd/) | Executable discovery, byte stdin, stream/status predicates, failure diagnostics. | Keep its command and assertion API. |
| [assert_fs](https://docs.rs/assert_fs/latest/assert_fs/) | Temporary paths, fixture copying, binary writes and file assertions. | Use for explicit mutable-file scenarios. |
| [trycmd](https://docs.rs/trycmd/latest/trycmd/) | Declarative cases, stream sidecars, output trees and snapshots. | Retain existing Rust cases instead of adding another format. |
| [snapbox](https://docs.rs/snapbox/latest/snapbox/assert/struct.Assert.html) | Snapshot comparison, configurable normalization and matching. | Existing byte fixtures need no additional snapshot layer. |

Cargo discovery belongs in the consuming package's integration test, where Cargo
provides the binary path. Resolving the shared library's own package would be
incorrect. [assert_cmd Cargo discovery](https://docs.rs/assert_cmd/latest/assert_cmd/cargo/index.html)

trycmd's default text normalization and optional stdout/stderr expectations need
care in exact compatibility tests. Its sandbox behavior also depends on fixture
configuration. Snapbox's default text assertions include wildcards and path/newline
normalization; these are different contracts from exact byte equality.
[trycmd format](https://docs.rs/trycmd/latest/trycmd/),
[snapbox assertions](https://docs.rs/snapbox/latest/snapbox/assert/struct.Assert.html)

## Timeout limit

Source inspection of assert_cmd 2.2.2 shows that the stdin writer is joined before
the timed child wait begins. A large write to a child that never reads may thus
block outside the command timeout. This is an inference from implementation,
not a documented process-tree deadline. Keep the outer nextest timeout and use a
specialized harness for adversarial or interactive processes.
[assert_cmd implementation](https://docs.rs/assert_cmd/2.2.2/src/assert_cmd/cmd.rs.html)
