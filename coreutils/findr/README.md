# findr

`findr` recursively lists paths filtered by entry type and filename regular expressions.

## Supported capabilities

- Search one or more starting paths (default: current directory).
- `-n`/`--name` matches entry basenames with Rust regular expressions.
- `-t`/`--type` selects directories (`d`), files (`f`), or symbolic links (`l`).
- Multiple names or types match any value within that group; both groups must match.

## Examples

From the workspace root, put starting paths before the filters:

```sh
cargo run -p findr -- coreutils/true -t f
cargo run -p findr -- coreutils/true -n '\.rs$'
```

## Differences and limits

Reference: the separate [GNU find manual](https://www.gnu.org/software/findutils/manual/).
Names are regexes, not GNU `find -name` shell patterns. There is no general
expression language, `-exec`, or deletion action. Traversal does not follow
nested symlink directories; traversal errors are printed without causing failure.

## Testing

Follow the [CLI testing skill](../../.agents/skills/cli-testing/SKILL.md)
for setup and reference-generation procedure.

Directory traversal order is unspecified. The CLI tests compare sorted line
multisets, preserving duplicate paths, rather than requiring the reference
filesystem's traversal order. Keep this exception local to traversal assertions.

[Workspace README](../../README.md)
