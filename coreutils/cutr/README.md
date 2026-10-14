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

## Testing

Follow the [CLI testing skill](../../.agents/skills/cli-testing/SKILL.md)
for setup and reference-generation procedure.

Character fixtures require a multibyte-aware BSD `cut` and a supported UTF-8
locale. Byte fixtures retain raw reference bytes, including partial UTF-8
sequences; the relevant assertions explicitly apply replacement decoding to
match `cutr`. The duplicate-position fixture is a manual expectation because
upstream `cut` deduplicates positions.

[Workspace README](../../README.md)
