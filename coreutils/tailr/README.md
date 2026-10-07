# tailr

`tailr` prints the end of files or stdin, or their contents starting at a chosen line or byte.

## Supported capabilities

- Print the last 10 lines by default; `-n`/`--lines` changes the count.
- `-c`/`--bytes` selects bytes instead; `+N` starts at one-based position N.
- Read multiple files and explicit stdin (`-`); `-q`/`--quiet` suppresses their headers.
- Copy selected content without UTF-8 decoding.

## Examples

From the workspace root:

```sh
cargo run -p tailr -- -n 5 Cargo.toml
printf 'first\nlast\n' | cargo run -p tailr -- -n 1 -
```

## Differences and limits

Reference: [GNU tail](https://www.gnu.org/software/coreutils/manual/html_node/tail-invocation.html).
At least one filename or `-` is required; omitting operands does not select stdin.
Named files must support seeking. Stdin is consumed through EOF: last-N mode
retains at most N bytes, or N lines plus the line being read, rather than buffering
all input unconditionally. Very long lines or large counts can still require
substantial memory. `+N` skips the prefix and streams the remainder; `+0` starts
at the beginning, while unsigned or negative zero produces no output. Repeated
`-` operands share stdin, so later occurrences encounter EOF after the first.
There is no follow (`-f`) mode or size suffix syntax. File-open errors do not by
themselves make the command fail.

[Workspace README](../../README.md)

## Correctness-checked benchmarks

The [tailr suite](benches/cli-bench.toml) runs through
[cli-bench](../../tools/cli-bench/README.md). Primary cases cover file lines, file bytes, a real finite pipe and tiny startup.
Confirmation uses a separate seed/shape plus 4,095/4,096/4,097 byte boundaries.
File/pipe results have separate I/O boundaries and no seek-tail throughput claim.

From the workspace root, after setting explicit GNU reference/tool paths where
applicable:

```sh
cargo build --locked --release -p cli-bench -p biggie -p tailr
target/release/cli-bench run -p tailr -a target/release/tailr \
  -x /absolute/path/to/GNU/tail -g target/release/biggie -m smoke
```

These smoke commands validate wiring; they do not demonstrate an optimization.
Select primary cases explicitly while tuning and use separate `confirm-*` cases
under full settings with a fixed previous revision after choosing the candidate.
See the [benchmark policy](../../docs/benchmarking.md) for acceptance and evidence.
