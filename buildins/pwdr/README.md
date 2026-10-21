# pwdr

`pwdr` prints the current working directory as an absolute path.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `-L` | Logical path, preserving valid `PWD` symlinks; default mode. |
| `-P` | Physical path with symlinks resolved; conflicts with `-L`. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

Logical mode falls back to the OS-reported directory when `PWD` is invalid.

## Before using it

Reference: [GNU pwd](https://www.gnu.org/software/coreutils/manual/html_node/pwd-invocation.html).
This implementation defaults to logical mode, unlike standalone GNU `pwd`'s
usual physical default. `-L` and `-P` are mutually exclusive rather than using
the last flag supplied. Paths containing non-Unicode bytes are displayed lossily.

[All commands](../../README.md)
