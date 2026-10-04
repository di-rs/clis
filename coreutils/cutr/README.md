# cutr

`cutr` selects fields, Unicode characters, or bytes from text input.

## Supported capabilities

- Choose exactly one of `-f`/`--fields`, `-c`/`--chars`, or `-b`/`--bytes`.
- Use one-based positions and closed ranges such as `1,3-5`.
- `-d`/`--delimiter` sets a single-byte field separator (default: tab).
- Read multiple files, or non-terminal stdin through `-` or no filenames.

## Examples

From the workspace root:

```sh
printf 'red,green,blue\n' | cargo run -p cutr -- -d , -f 1,3
printf 'abcdef\n' | cargo run -p cutr -- -c 2-4
```

## Differences and limits

Unlike [GNU cut](https://www.gnu.org/software/coreutils/manual/html_node/cut-invocation.html),
field mode uses CSV parsing and writing, including quoting and consistent field
counts. Open-ended ranges and equal-endpoint ranges such as `2-2` are rejected;
selections retain the requested order and duplicates. Input must be UTF-8;
byte selections that split a character are decoded with replacement characters.

[Workspace README](../../README.md)
