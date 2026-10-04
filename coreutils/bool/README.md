# bool

Two small binaries return success (`true`) or failure (`false`) without output.

## Behavior

- `true` exits with status 0 without output.
- `false` exits normally with status 1 without output.
- Neither binary parses arguments or implements help/version flags.

## Examples

From the workspace root:

```sh
cargo run -p bool --bin true
cargo run -p bool --bin false
```

The second command returns status 1; this is its intended result.

Reference: GNU Coreutils manuals for [true](https://www.gnu.org/software/coreutils/manual/html_node/true-invocation.html)
and [false](https://www.gnu.org/software/coreutils/manual/html_node/false-invocation.html).

[Workspace README](../../README.md)
