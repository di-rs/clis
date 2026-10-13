# false

Return failure without output.

## Behavior

- Exits normally with status 1 without output.
- Ignores arguments and does not implement help/version flags.

## Example

From the workspace root:

```sh
cargo run -p false
```

The command returns status 1; this is its intended result.

Reference: GNU Coreutils manual for [false](https://www.gnu.org/software/coreutils/manual/html_node/false-invocation.html).

[Workspace README](../../README.md)
