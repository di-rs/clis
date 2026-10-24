# grepr

`grepr` searches UTF-8 text for lines matching a Rust regular expression.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `PATTERN` | Required Rust regular expression. |
| `FILES...` | Input files; default `-` reads non-terminal stdin. |
| `-i, --insensitive` | Ignore case. |
| `-r, --recursive` | Search nested files. |
| `-c, --count` | Count selected lines, not individual occurrences. |
| `-v, --invert-match` | Select nonmatching lines. |
| `--verbose` | Increase stderr logging verbosity; repeatable. Default threshold is error. |
| `-q, --quiet` | Decrease stderr logging verbosity; repeatable; conflicts with `--verbose`. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Before using it

Reference: the separate [GNU grep manual](https://www.gnu.org/software/grep/manual/grep.html).
Regex syntax is not GNU basic regular-expression syntax. No-match results still
exit successfully. File discovery/open errors go to stderr and make the final
exit status 1; valid operands are still processed. SIGINT uses the operating
system default interruption behavior without printing into search results.

This is not a binary-file search tool.

Tutorial credit: [Command Line Applications in Rust](https://rust-cli.github.io/book/tutorial/packaging.html).

## Test notes

Reference `grep` exits with status 1 when no lines match, while `grepr`
returns status 0. The [case catalog](tests/cases.toml) declares both statuses
and checks the reference's stderr, using macOS `/usr/bin/grep`. Use
[cli-testing](../../tools/cli-testing/README.md) to inspect or stage reference
output. Stdin recipes list input files in their concatenation order.

The retained recursive-count fixture uses explicit operands in its historical
order so its bytes do not depend on directory enumeration. Native traversal,
signal, logging, and temporary-filesystem scenarios remain in
[tests/cli.rs](tests/cli.rs).

[All commands](../../README.md)
