# mkdirr

`mkdirr` creates directories, including missing parent directories by default.

## Supported capabilities

- Accept one or more directory paths.
- Parent creation is enabled by default; the CLI also accepts `-p`.
- Existing directories are accepted; existing files are errors.
- Stop and report an error when creation fails.

## Example

From the workspace root, using a destination that does not already exist:

```sh
cargo run -p mkdirr -- mkdirr-example/nested
```

This creates directories on disk.

## Differences and limits

Unlike [GNU mkdir](https://www.gnu.org/software/coreutils/manual/html_node/mkdir-invocation.html),
parent creation needs no `-p`. No permission-mode option is implemented.
At least one directory path is required.

[Workspace README](../../README.md)

## Correctness-checked benchmarks

The [mkdirr suite](benches/cli-bench.toml) runs through
[cli-bench](../../tools/cli-bench/README.md). Biggie generates safe path records for four primary and eight confirmation paths.
Absent and already-existing states are separate cases. The harness resets owned
scratch before every invocation and verifies the exact parent tree and modes.

From the workspace root, after setting explicit GNU reference/tool paths where
applicable:

```sh
cargo build --locked --release -p cli-bench -p biggie -p mkdirr
target/release/cli-bench run -p mkdirr -a target/release/mkdirr \
  -x /absolute/path/to/GNU/mkdir -g target/release/biggie -m smoke
```

These smoke commands validate wiring; they do not demonstrate an optimization.
Select primary cases explicitly while tuning and use separate `confirm-*` cases
under full settings with a fixed previous revision after choosing the candidate.
See the [benchmark policy](../../docs/benchmarking.md) for acceptance and evidence.
