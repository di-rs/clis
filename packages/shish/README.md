# Shish

Shish is Sanctuary's shell application, embedding Brush for parsing, expansion,
shell state, execution, and terminal editing. It defaults to Brush's sh/POSIX
settings. This is a configuration choice, not a claim of full POSIX conformance.

## Run commands

From the repository root:

```sh
cargo run -p shish -- -c 'printf "%s\n" "hello world"'
cargo run -p shish -- script.sh first-argument
printf 'echo hello\n' | cargo run -p shish -- -s
```

Use `--help` for arguments and `--version` for the package version.

| Invocation | Behavior |
| --- | --- |
| No command or script | Read stdin; enable terminal editing when stdin and stderr are terminals. |
| `-i` | Force interactive shell settings; terminal editing requires terminal stdin. |
| `-c COMMAND [NAME [ARG...]]` | Execute a string; `NAME` becomes `$0`, remaining operands become `$1` onward. |
| `SCRIPT [ARG...]` | Read a script; its filename becomes `$0`. |
| `-s [ARG...]` | Read commands from stdin with operands as positional parameters. |
| `-n` | Check the selected command, script, or stdin without execution or redirection effects. |
| `--posix` | Explicitly select the default sh/POSIX settings. |

Noninteractive execution prints no prompt. Interactive sessions use `PS1` when
provided, otherwise the current directory's basename followed by ` > `. Brush
provides shell commands including `cd`, `exit`, and `jobs`; external programs use
ordinary `PATH` lookup. There is no discovery of repository build directories.

EOF returns the last command's status. `false` returns 1 and does not itself stop
the shell; `exit` ends execution. Missing commands return 127. Syntax errors return
2 and write a diagnostic to stderr; successful syntax checks are silent.

Startup deliberately skips profile and rc files, including inherited `ENV` and
`BASH_ENV`. Source configuration explicitly with the `.` command if needed. This
is an integration policy to track separately from future strict compatibility.
The initial integration targets Unix platforms; local verification is on macOS,
with Linux verification delegated to CI.

Shish runs with the invoking user's privileges. It supplies neither sandbox
isolation nor recovery of interrupted processes. Boxie owns isolation and lifecycle
recovery; its integration with shish remains a separate design stage.

See [architecture](docs/architecture.md) for the Brush boundary and
[platform direction](../../docs/platform-direction.md) for the compatibility policy.
The compatibility inventory and terminal behavior audit follow in the foundation
stack; untested features are not yet a support promise.

[All projects](../../README.md)
