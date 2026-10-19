# touchr

`touchr` creates missing files and sets file modification times.

## Supported capabilities

- Accept multiple file paths; existing file contents are left unchanged.
- Use the current time by default, or set a timestamp with `-t`.
- `-c` skips creation of missing files.

## Examples

From the workspace root, using a disposable example file:

```sh
cargo run -p touchr -- touchr-example.txt
cargo run -p touchr -- -c -t 20260102123456 touchr-example.txt
```

These commands create a file and change its modification time.

## Differences and limits

Reference: [GNU touch](https://www.gnu.org/software/coreutils/manual/html_node/touch-invocation.html).
Only modification time is explicitly set, not access time. The supported timestamp
forms are `YYYYMMDDhhmm` and `YYYYMMDDhhmmss`, interpreted as UTC, despite the help
text advertising `[[CC]YY]MMDDhhmm[.ss]`. No local-time parsing, reference-file option,
or separate access-time mode is implemented.

[Workspace README](../../README.md)
