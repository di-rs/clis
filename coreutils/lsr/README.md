# lsr

`lsr` lists files and immediate directory contents on Unix systems.

## Supported capabilities

- Accept multiple paths, defaulting to the current directory.
- `-a`/`--all` includes hidden entries.
- `-l`/`--long` shows permissions, link count, owner, group, size, and modification time.

## Examples

From the workspace root:

```sh
cargo run -p lsr -- coreutils/bool
cargo run -p lsr -- -la coreutils/bool
```

## Differences and limits

Reference: [GNU ls](https://www.gnu.org/software/coreutils/manual/html_node/ls-invocation.html).
Output uses paths rather than a columnar filename layout and is not sorted.
There is no recursive mode; `-a` does not synthesize `.` or `..` entries.
Long listings follow symlink metadata rather than describing links themselves,
and group IDs are currently looked up as user IDs, so group names may be wrong.

[Workspace README](../../README.md)
