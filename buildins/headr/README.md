# headr

`headr` prints the beginning of files or piped input.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `FILE...` | Input files; default `-` reads non-terminal stdin. Multiple files get headers. |
| `-n, --lines N` | Positive line count; default 10. |
| `-c, --bytes N` | Positive byte count; conflicts with explicit `-n`. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Before using it

Reference: [GNU head](https://www.gnu.org/software/coreutils/manual/html_node/head-invocation.html).
Zero and negative counts and size suffixes are not supported. Line mode requires
UTF-8; byte mode uses lossy UTF-8 decoding, so it does not preserve arbitrary bytes.
File-open errors go to stderr; remaining operands are processed and the final
exit status is 1.

## Test notes

Reference `head` emits raw bytes, including partial UTF-8 sequences in byte
mode. Keep those fixture bytes unchanged. The assertions explicitly decode
expected bytes with replacement to match `headr`'s intentional UTF-8 behavior.

[All commands](../../README.md)
