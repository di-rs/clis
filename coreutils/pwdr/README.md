# pwdr

`pwdr` prints the current working directory as an absolute path.

## Supported capabilities

- Logical mode is the default; `-L` selects it explicitly.
- Use `PWD` only when it is absolute and resolves to the current directory;
  otherwise fall back to the OS-reported current directory.
- `-P` prints the canonical physical path, resolving symbolic links.

## Examples

From the workspace root:

```sh
cargo run -p pwdr -- -L
cargo run -p pwdr -- -P
```

## Differences and limits

Reference: [GNU pwd](https://www.gnu.org/software/coreutils/manual/html_node/pwd-invocation.html).
This implementation defaults to logical mode, unlike standalone GNU `pwd`'s
usual physical default. `-L` and `-P` are mutually exclusive rather than using
the last flag supplied. Paths containing non-Unicode bytes are displayed lossily.

[Workspace README](../../README.md)
