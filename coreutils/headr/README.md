# headr

`headr` prints the beginning of files or piped input.

## Supported capabilities

- Print 10 lines by default, or a positive count with `-n`/`--lines`.
- Select a positive byte count instead with `-c`/`--bytes`.
- Read multiple files with headers, or non-terminal stdin through `-` or no filenames.

## Examples

From the workspace root:

```sh
cargo run -p headr -- -n 5 Cargo.toml
printf 'abcdef\n' | cargo run -p headr -- -c 3
```

## Differences and limits

Reference: [GNU head](https://www.gnu.org/software/coreutils/manual/html_node/head-invocation.html).
Zero and negative counts and size suffixes are not supported. Line mode requires
UTF-8; byte mode uses lossy UTF-8 decoding, so it does not preserve arbitrary bytes.
File-open errors are printed but do not by themselves make the command fail.

[Workspace README](../../README.md)
