# wcr

`wcr` counts lines, words, characters, and bytes in UTF-8 text.

## Supported capabilities

- Default output reports lines, words, and bytes.
- Select counts with `-l`/`--lines`, `-w`/`--words`, `-m`/`--chars`, or `-c`/`--bytes`.
- Read multiple files and print totals, or use non-terminal stdin (`-` or no filenames).
- Character and byte flags are mutually exclusive.

## Examples

From the workspace root:

```sh
cargo run -p wcr -- Cargo.toml
printf 'hello world\n' | cargo run -p wcr -- -lw
```

## Differences and limits

Reference: [GNU wc](https://www.gnu.org/software/coreutils/manual/html_node/wc-invocation.html).
A final unterminated line counts as a line, unlike GNU `wc`'s newline count.
Words use Rust Unicode whitespace splitting; characters are Unicode scalar values,
not grapheme clusters. Even byte-only counting requires valid UTF-8 input.
File-open errors are printed but do not by themselves make the command fail.

[Workspace README](../../README.md)
