# Biggie

`biggie` generates a text file of random alphanumeric words for testing other programs.

## Supported capabilities

- Choose an output filename (default: `out.txt`).
- `-n`/`--lines` sets a positive line count (default: 100,000).
- Each line contains 7–14 space-separated words, each 2–11 characters long.

## Example

From the workspace root, using a disposable output path:

```sh
cargo run -p biggie -- -n 100 biggie-example.txt
```

## Limits

The destination is created or truncated; existing contents are overwritten.
Output goes to a file, not stdout, and there is no seed option for reproducible
output. Line count does not specify an exact file size. There is no direct GNU
Coreutils counterpart; `cargo run -p biggie -- --help` lists this program's options.

[Workspace README](../README.md)
