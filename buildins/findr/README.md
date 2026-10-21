# findr

`findr` recursively lists paths filtered by entry type and filename regular expressions.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `PATHS...` | Starting paths; default current directory. Put these before the filters. |
| `-n, --name [PATTERN...]` | Repeatable filename regex filters; match any supplied pattern. |
| `-t, --type [TYPE...]` | Repeatable entry types: `d` directory, `f` file, `l` symlink. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

When both name and type filters are supplied, an entry must satisfy both groups.

## Before using it

Reference: the separate [GNU find manual](https://www.gnu.org/software/findutils/manual/).
Names are regexes, not GNU `find -name` shell patterns. There is no general
expression language, `-exec`, or deletion action. Traversal does not follow
nested symlink directories; traversal errors are printed without causing failure.

## Test notes

Directory traversal order is unspecified. The CLI tests compare sorted line
multisets, preserving duplicate paths, rather than requiring the reference
filesystem's traversal order. Keep this exception local to traversal assertions.

[All commands](../../README.md)
