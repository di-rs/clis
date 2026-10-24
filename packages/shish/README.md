# Shish

Shish is Sanctuary's shell for running commands, scripts, and interactive
sessions. It defaults to sh/POSIX settings; full POSIX conformance is not yet
guaranteed.

## Run commands

From the repository root:

```sh
cargo run -p shish -- -c 'printf "%s\n" "hello world"'
cargo run -p shish -- script.sh first-argument
printf 'echo hello\n' | cargo run -p shish -- -s
```

The first command prints `hello world`.

## Invocation

Use `--help` for arguments and `--version` for the package version.
The root `make install-rust` target also installs shish into `~/.local/bin` (or
`PREFIX/bin` when overridden).

| Invocation | Behavior |
| --- | --- |
| No command or script | Read stdin; enable terminal editing when stdin and stderr are terminals. |
| `-i` | Force interactive shell settings; terminal editing requires terminal stdin. |
| `-c COMMAND [NAME [ARG...]]` | Execute a string; `NAME` becomes `$0`, remaining operands become `$1` onward. |
| `SCRIPT [ARG...]` | Read a script; its filename becomes `$0`. |
| `-s [ARG...]` | Read commands from stdin with operands as positional parameters. |
| `-n` | Check the selected command, script, or stdin without execution or redirection effects. |
| `--posix` | Explicitly select the default sh/POSIX settings. |
| `--bash` | Opt into Brush's Bash settings and builtins such as `declare`, `complete`, and `history`; conflicts with `--posix`. |

## Commands and exit status

Noninteractive execution prints no prompt. Interactive sessions use `PS1` when
provided, otherwise the current directory's basename (`~` at HOME) followed by ` > `.
Builtin commands include `cd`, `exit`, and `jobs`; external programs use
ordinary `PATH` lookup. There is no discovery of repository build directories.
`true` and `false` use Sanctuary's implementations in process, ignore arguments,
and return status without terminating shish or creating standalone utility logs.
They are the only Sanctuary utilities integrated as shell builtins.

EOF returns the last command's status. `false` returns 1 and does not itself stop
the shell; `exit` ends execution. Missing commands return 127. Syntax errors return
2 and write a diagnostic to stderr; successful syntax checks are silent.

## Startup and configuration

Startup skips profile and rc files, including inherited `ENV` and
`BASH_ENV`. Source configuration explicitly with the `.` command if needed. This
is a known difference from strict shell compatibility.

## Interactive history

Use `--bash` for completion specifications and history commands. The current
history adapter supports `history -r [file]`, `-w [file]`, and `-a [file]` with
paths relative to the shell directory. Use `./` for history filenames beginning
with a dash. Automatic exit saving appends even when `histappend` is unset; use `history -w && unset HISTFILE` before exit when
you need replacement. History is a plaintext convenience, not a durable execution
record, and can contain command-line secrets. Keep its location private.

## Compatibility and further reading

The integration targets Unix platforms. The
[compatibility inventory](docs/compatibility.md#reading-the-evidence) records
Linux and macOS CI evidence. gVisor integration remains unverified.

Shish runs with the invoking user's privileges. It supplies neither sandbox
isolation nor recovery of interrupted processes. Boxie owns isolation and lifecycle
recovery; its integration with shish remains a separate design stage.

- [Compatibility](docs/compatibility.md): tested features, known gaps, and workarounds.
- [Testing](docs/testing.md): package checks and terminal test conventions.
- [Architecture](docs/architecture.md): runtime and adapter responsibilities.
- [Durability design](docs/durability.md): proposed Boxie integration and recovery requirements.

[All projects](../../README.md)
