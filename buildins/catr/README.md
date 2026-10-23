# catr

`catr` concatenates UTF-8 text files or piped input to standard output.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `FILE...` | Input files; default `-` reads non-terminal stdin. |
| `-n, --number` | Number every line; conflicts with `-b`. |
| `-b, --number-nonblank` | Number nonblank lines. |
| `-s, --squeeze-blank` | Collapse adjacent empty lines. |
| `-u, --unbuffered` | Write without the additional output buffer. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Before using it

Compared with [GNU cat](https://www.gnu.org/software/coreutils/manual/html_node/cat-invocation.html),
input must be UTF-8 and numbering restarts for each file.
Output is buffered by default; `-u` disables that buffer. File-open errors go to
stderr; remaining operands are processed and the final exit status is 1.

## Test notes

Multi-file numbering fixtures use BSD `cat`, which restarts numbering for
each file. GNU `cat` continues numbering across files and produces different
expectations for those cases.

The [case catalog](tests/cases.toml) connects each invocation to its inputs,
expected bytes, and explicit macOS `/bin/cat` reference. Use
[cli-fixtures](../../tools/cli-fixtures/README.md) to inspect or stage reference
output. Native error and boundary tests remain in [tests/cli.rs](tests/cli.rs).

[All commands](../../README.md)
