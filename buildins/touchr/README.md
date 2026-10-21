# touchr

`touchr` creates missing files and sets file modification times.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `FILES...` | One or more required paths; create missing files and update modification times. |
| `-c` | Do not create missing files. |
| `-t TIMESTAMP` | UTC `YYYYMMDDhhmm` or `YYYYMMDDhhmmss`; default current time. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Before using it

Reference: [GNU touch](https://www.gnu.org/software/coreutils/manual/html_node/touch-invocation.html).
Only modification time is explicitly set, not access time. The supported timestamp
forms are `YYYYMMDDhhmm` and `YYYYMMDDhhmmss`, interpreted as UTC, despite the help
text advertising `[[CC]YY]MMDDhhmm[.ss]`. No local-time parsing, reference-file option,
or separate access-time mode is implemented.

[All commands](../../README.md)
