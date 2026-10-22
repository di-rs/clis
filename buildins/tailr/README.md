# tailr

`tailr` prints the end of files or stdin, or their contents starting at a chosen line or byte.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `FILES...` | One or more required inputs; use `-` explicitly for stdin. |
| `-n, --lines N` | Last N lines; default 10. `+N` starts at one-based line N. |
| `-c, --bytes N` | Last N bytes, or start at `+N`; conflicts with explicit `-n`. |
| `-q, --quiet` | Suppress multi-file headers. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Before using it

Reference: [GNU tail](https://www.gnu.org/software/coreutils/manual/html_node/tail-invocation.html).
At least one filename or `-` is required; omitting operands does not select stdin.
Named files must support seeking. Stdin is consumed through EOF: last-N mode
retains at most N bytes, or N lines plus the line being read, rather than buffering
all input unconditionally. Very long lines or large counts can still require
substantial memory. `+N` skips the prefix and streams the remainder; `+0` starts
at the beginning, while unsigned or negative zero produces no output. Repeated
`-` operands share stdin, so later occurrences encounter EOF after the first.
There is no follow (`-f`) mode or size suffix syntax. File-open errors go to stderr; remaining operands are processed and the final
exit status is 1.

Benchmark suite: [benches/cli-bench.toml](benches/cli-bench.toml).
Use [cli-bench](../../tools/cli-bench/README.md) with `--reference` for a system
command or `--baseline` for a previous executable.

## Test notes

Existing multi-file fixtures use BSD `tail` header and empty-file behavior.
Keep byte-mode output as raw bytes and review the selected reference's behavior
before replacing those expectations.

[All commands](../../README.md)
