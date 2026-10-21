# wcr

`wcr` counts lines, words, characters, and bytes in UTF-8 text.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `FILE...` | Input files; default `-` reads non-terminal stdin. Multiple files get totals. |
| `-l, --lines` | Show line count. |
| `-w, --words` | Show word count. |
| `-m, --chars` | Show Unicode scalar count; conflicts with `-c`. |
| `-c, --bytes` | Show byte count; conflicts with `-m`. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

With no count flags, show lines, words, and bytes.

## Before using it

Reference: [GNU wc](https://www.gnu.org/software/coreutils/manual/html_node/wc-invocation.html).
A final unterminated line counts as a line, unlike GNU `wc`'s newline count.
Words use Rust Unicode whitespace splitting; characters are Unicode scalar values,
not grapheme clusters. Even byte-only counting requires valid UTF-8 input.
File-open errors are printed but do not by themselves make the command fail.

## Test notes

The existing reference fixtures use BSD `wc` column widths. Use a UTF-8
locale supported by the reference when regenerating `-m` character counts;
`LC_ALL=C` changes those counts. GNU `wc` formatting is not interchangeable
with these fixtures.

[All commands](../../README.md)
