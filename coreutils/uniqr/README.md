# uniqr

`uniqr` collapses consecutive runs of matching text lines.

## Supported capabilities

- Read one file or non-terminal stdin (`-`, also the default input).
- `-c`/`--count` prefixes each retained line with its run length.
- An optional second positional argument writes to an output file instead of stdout.

## Examples

From the workspace root:

```sh
printf 'red\nred\nblue\n' | cargo run -p uniqr -- -c
cargo run -p uniqr -- Cargo.toml
```

## Differences and limits

Reference: [GNU uniq](https://www.gnu.org/software/coreutils/manual/html_node/uniq-invocation.html).
Comparisons ignore the terminating newline but preserve spaces, tabs, and carriage
returns. Output keeps the first line of each run unchanged. Input must be UTF-8; all runs are stored in
memory before output. Only adjacent matches are collapsed. Output files are
truncated, so do not use the input path as the output path.

[Workspace README](../../README.md)
