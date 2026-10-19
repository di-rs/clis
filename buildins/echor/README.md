# echor

`echor` prints its text arguments separated by spaces.

## Supported capabilities

- Append a newline by default; `-n` omits it.
- Use `--` to separate options from text beginning with a hyphen.

## Examples

From the workspace root:

```sh
cargo run -p echor -- hello world
cargo run -p echor -- -n -- -literal
```

## Differences and limits

Reference: [GNU echo](https://www.gnu.org/software/coreutils/manual/html_node/echo-invocation.html).
At least one text argument is required. There is no escape-sequence interpretation
or `-e`/`-E` option; this is not a replacement for shell-specific `echo` behavior.

## Testing

Follow the [CLI testing skill](../../.agents/skills/cli-testing/SKILL.md)
for setup and reference-generation procedure.

The generator uses an external `echo`; choose the reference executable through
`PATH` rather than relying on a shell builtin. The `-n` fixtures intentionally
have no final newline.

[Workspace README](../../README.md)
