# catr

`catr` concatenates UTF-8 text files or piped input to standard output.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `FILE...` | Input files; default `-` reads non-terminal stdin. |
| `-n, --number` | Number every line; conflicts with `-b`. |
| `-b, --number-nonblank` | Number nonblank lines. |
| `-s, --squeeze-blank` | Collapse adjacent empty lines. |
| `-u, --unbuffered` | Currently enables buffering, despite its name; see the limitation below. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Before using it

Compared with [GNU cat](https://www.gnu.org/software/coreutils/manual/html_node/cat-invocation.html),
input must be UTF-8 and numbering restarts for each file.
The advertised `-u`/`--unbuffered` flag actually
selects a buffered writer; it does not disable buffering. File-open errors are
printed but do not by themselves make the command fail.

## Test notes

Multi-file numbering fixtures use BSD `cat`, which restarts numbering for
each file. GNU `cat` continues numbering across files and produces different
expectations for those cases.

[All commands](../../README.md)
