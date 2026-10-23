# Shish

Shish is Sanctuary's shell application. This initial import runs external commands;
the existing parser and executor are being replaced with Brush.

## Run a command

From the repository root:

```sh
cargo run -p shish -- -c '/bin/echo hello'
```

Use `--help` for arguments and `--version` for the package version. With no
arguments, shish runs its original prompt loop. This migration baseline is not
POSIX conformant: shell-state builtins, EOF handling, quoting, and redirection
still need correction. Do not use the imported interactive loop for unattended
input until the subsequent repair lands.

No upstream utility crates are imported. External commands come from the host.
Shish runs with the invoking user's privileges; it is not a sandbox and does not
make commands durable. Boxie owns sandbox isolation and lifecycle recovery.

See [architecture](docs/architecture.md) for the migration boundary and
[platform direction](../../docs/platform-direction.md) for the compatibility policy.

[All projects](../../README.md)

