# catr

`catr` concatenates UTF-8 text files or piped input to standard output.

## Supported capabilities

- Read multiple files; omitted filenames or `-` select non-terminal stdin.
- Number all lines with `-n` or nonblank lines with `-b` (mutually exclusive).
- Squeeze adjacent empty lines with `-s`.

## Examples

From the workspace root:

```sh
cargo run -p catr -- -n Cargo.toml
printf 'one\n\ntwo\n' | cargo run -p catr -- -b
```

## Differences and limits

Compared with [GNU cat](https://www.gnu.org/software/coreutils/manual/html_node/cat-invocation.html),
input must be UTF-8 and numbering restarts for each file.
The advertised `-u`/`--unbuffered` flag actually
selects a buffered writer; it does not disable buffering. File-open errors are
printed but do not by themselves make the command fail.

## Testing

Follow the [CLI testing skill](../../.agents/skills/cli-testing/SKILL.md)
for setup and reference-generation procedure.

Multi-file numbering fixtures use BSD `cat`, which restarts numbering for
each file. GNU `cat` continues numbering across files and produces different
expectations for those cases.

[Workspace README](../../README.md)
