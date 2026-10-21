# commr

`commr` compares two sorted text inputs and prints lines unique to either input or common to both.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `FILE_1 FILE_2` | Two required sorted inputs; at most one may be `-` for non-terminal stdin. |
| `-1` | Hide lines unique to the first input. |
| `-2` | Hide lines unique to the second input. |
| `-3` | Hide lines common to both inputs. |
| `-i, --insensitive` | Lowercase lines before comparison and output. |
| `-d, --output-delimiter TEXT` | Column separator; default tab. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Before using it

Reference: [GNU comm](https://www.gnu.org/software/coreutils/manual/html_node/comm-invocation.html).
Inputs must already be sorted using Rust string ordering, not locale collation;
there is no sortedness check. Case-insensitive mode changes the printed text.
Reading stops at the first decoding or I/O error in each input without reporting it.

## Test notes

Use BSD `comm` for the `-i` fixtures; GNU `comm` has no equivalent flag.
Generate with `LC_ALL=C` so collation matches the byte ordering used by the
committed inputs and `commr`. Custom-delimiter expectations replace reference
tabs locally.

[All commands](../../README.md)
