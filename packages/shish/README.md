# Shish

Shish is Sanctuary's shell application. This initial import runs external commands;
the existing parser and executor are being replaced with Brush.

## Run a command

From the repository root:

```sh
cargo run -p shish -- -c '/bin/echo hello'
```

Use `--help` for arguments and `--version` for the package version. With no
arguments, shish reads commands from stdin and shows prompts only on a terminal.
EOF exits with the last command's status. This migration baseline is not POSIX
conformant: shell-state builtins, quoting, and redirection still need correction.

Use `-n -c 'COMMAND'` or pipe a script into `shish -n` to check syntax without
execution. This path uses Brush's sh/POSIX parser settings, reports syntax errors
on stderr, and exits with status 2 on invalid syntax. Successful checks are silent.
Syntax acceptance does not establish that the migration runtime can execute it.

No upstream utility crates are imported. External commands come from the host.
Shish runs with the invoking user's privileges; it is not a sandbox and does not
make commands durable. Boxie owns sandbox isolation and lifecycle recovery.

See [architecture](docs/architecture.md) for the migration boundary and
[platform direction](../../docs/platform-direction.md) for the compatibility policy.

[All projects](../../README.md)

