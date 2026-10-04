# commr

`commr` compares two sorted text inputs and prints lines unique to either input or common to both.

## Supported capabilities

- Three output columns, suppressed individually with `-1`, `-2`, and `-3`.
- `-d`/`--output-delimiter` changes the default tab separator.
- `-i`/`--insensitive` lowercases lines before comparison and output.
- One input may be `-` for non-terminal stdin; both cannot be stdin.

## Example

From the workspace root (both streams are sorted):

```sh
printf 'a\nb\n' | cargo run -p commr -- - /dev/null
```

## Differences and limits

Reference: [GNU comm](https://www.gnu.org/software/coreutils/manual/html_node/comm-invocation.html).
Inputs must already be sorted using Rust string ordering, not locale collation;
there is no sortedness check. Case-insensitive mode changes the printed text.
Reading stops at the first decoding or I/O error in each input without reporting it.

[Workspace README](../../README.md)
