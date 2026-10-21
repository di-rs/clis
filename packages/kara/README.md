# Kara

Kara is a small terminal text editor. It starts in navigation mode; switch to
editing mode to type, then return to navigation to save or quit.

## Edit a file

From the repository root, open a disposable file in an interactive terminal:

```sh
cargo run -p kara -- scratch.txt
```

Press `i` to type, Esc to return to navigation, Ctrl-S to save, and Ctrl-Q to quit.
Saving replaces the named file after writing a temporary file successfully; there is no undo.

## Arguments

| Argument | Meaning |
| --- | --- |
| `FILE` | Optional first argument: file to edit. Omit it for an unnamed buffer. |
| `-h, --help` | Show help without opening a terminal. |
| `-V, --version` | Show version without opening a terminal. |
| `--` | End option parsing for a filename beginning with `-`. |

Extra operands and invalid arguments exit with status 2; startup or event-loop
failures exit with status 1. Normal quit exits with status 0.

## Controls

| Action | Keys |
| --- | --- |
| Move | Arrow keys or vim-like motions in navigation mode |
| Start editing | `i` |
| Return to navigation | Esc or Ctrl-C |
| Save | Ctrl-S; unnamed buffers prompt for a filename |
| Enter a command | `:` from navigation, then Enter to run it |
| Quit | Ctrl-Q; press again consecutively to discard unsaved changes |

Commands include `w` to save, `w filename` to name an unnamed buffer, `q` to
request quit, and `q!` to discard changes and quit.

## Known limitations

`w other-file` saves to that destination and makes it the active file only after
success. Command-bar filenames may contain spaces; everything after the first
space following `w` is the literal filename, without shell quoting or expansion. Failed saves leave the
buffer identity and modified state intact and display an error.

Saving uses a temporary file in the destination directory and an atomic rename.
Existing symlinks with valid targets are resolved; permissions are preserved.
Dangling symlinks are rejected without replacing the link or creating its target.
Read-only destinations and existing non-regular files (including FIFOs, sockets,
and devices) are rejected. On Unix, the parent directory is synced
after replacement.
Replacement breaks hard-link identity and does not preserve all extended metadata.
New files are private to their owner. Saving adds line terminators rather than
preserving the original line endings.

## Develop Kara

For editor changes, test navigation, typing, saving, quitting, and terminal
restoration in a PTY or real terminal. The startup E2E checks help, arguments, and failure when
there is no usable terminal; it does not cover editing.

Based on [Philipp Flenker's Hecto guide](https://philippflenker.com/hecto/).
[Architecture](docs/architecture.md) explains the implementation and boundaries.

[All projects](../../README.md)

## Install and verify

From the repository root, `make install-rust` installs Biggie, Parsu, and Kara into
`~/.local/bin` (override with `PREFIX=/path`). Add that directory to `PATH`.
Run `kara --help` from any directory. Kara is local and requires no authentication.

Run the Unix PTY regressions from the repository root after building Kara:

```sh
python3 packages/kara/tests/pty_smoke.py "$PWD/target/debug/kara"
```
