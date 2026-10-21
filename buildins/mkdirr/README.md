# mkdirr

`mkdirr` creates directories, including missing parent directories by default.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `DIRS...` | One or more required directory paths. |
| `-p` | Create missing parents; already enabled by default. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

Existing directories are accepted. Existing files or creation failures stop the command with an error.

## Before using it

Unlike [GNU mkdir](https://www.gnu.org/software/coreutils/manual/html_node/mkdir-invocation.html),
parent creation needs no `-p`. No permission-mode option is implemented.
At least one directory path is required.

[All commands](../../README.md)
