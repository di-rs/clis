# echor

`echor` prints its text arguments separated by spaces.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `TEXT...` | One or more required text arguments, joined with spaces. |
| `-n` | Omit the final newline. |
| `--` | End option parsing so later arguments may start with a hyphen. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Before using it

Reference: [GNU echo](https://www.gnu.org/software/coreutils/manual/html_node/echo-invocation.html).
At least one text argument is required. There is no escape-sequence interpretation
or `-e`/`-E` option; this is not a replacement for shell-specific `echo` behavior.

## Test notes

The generator uses an external `echo`; choose the reference executable through
`PATH` rather than relying on a shell builtin. The `-n` fixtures intentionally
have no final newline.

[All commands](../../README.md)
