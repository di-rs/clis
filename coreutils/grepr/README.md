# grepr

`grepr` searches UTF-8 text for lines matching a Rust regular expression.

## Supported capabilities

- Search files or non-terminal stdin (`-`, also the default input).
- `-i`/`--insensitive` ignores case; `-v`/`--invert-match` selects nonmatching lines.
- `-c`/`--count` counts selected lines, not individual pattern occurrences.
- `-r`/`--recursive` searches nested files, skipping directory entries themselves.

## Examples

From the workspace root:

```sh
cargo run -p grepr -- 'workspace' Cargo.toml
printf 'Hello\nworld\n' | cargo run -p grepr -- -i hello
```

## Differences and limits

Reference: the separate [GNU grep manual](https://www.gnu.org/software/grep/manual/grep.html).
Regex syntax is not GNU basic regular-expression syntax. No-match results still
exit successfully; file discovery/open errors can also leave a success status.

This is not a binary-file search tool.

Tutorial credit: [Command Line Applications in Rust](https://rust-cli.github.io/book/tutorial/packaging.html).

[Workspace README](../../README.md)
