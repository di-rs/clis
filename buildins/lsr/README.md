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
and group IDs are currently looked up as user IDs, so group names may be wrong.

[All commands](../../README.md)
