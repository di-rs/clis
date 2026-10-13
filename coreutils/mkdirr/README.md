# mkdirr

`mkdirr` creates directories, including missing parent directories by default.

## Supported capabilities

- Accept one or more directory paths.
- Parent creation is enabled by default; the CLI also accepts `-p`.
- Existing directories are accepted; existing files are errors.
- Stop and report an error when creation fails.

## Example

From the workspace root, using a destination that does not already exist:

```sh
cargo run -p mkdirr -- mkdirr-example/nested
```

This creates directories on disk.

## Differences and limits

Unlike [GNU mkdir](https://www.gnu.org/software/coreutils/manual/html_node/mkdir-invocation.html),
parent creation needs no `-p`. No permission-mode option is implemented.
At least one directory path is required.

[Workspace README](../../README.md)
