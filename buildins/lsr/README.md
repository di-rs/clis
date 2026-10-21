# lsr

`lsr` lists files and immediate directory contents on Unix systems.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `PATHS...` | Files or directories; default current directory. |
| `-a, --all` | Include hidden entries. |
| `-l, --long` | Show permissions, links, owner, group, size, and modification time. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Before using it

Reference: [GNU ls](https://www.gnu.org/software/coreutils/manual/html_node/ls-invocation.html).
Output uses paths rather than a columnar filename layout and is not sorted.
There is no recursive mode; `-a` does not synthesize `.` or `..` entries.
Long listings follow symlink metadata rather than describing links themselves,
and group names are resolved from group IDs, falling back to numeric IDs.
Invalid operands go to stderr; other operands are processed and the final status
is 1.

[All commands](../../README.md)
