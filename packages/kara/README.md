# Kara

Kara is a small terminal text editor. It starts in navigation mode; switch to
editing mode to type, then return to navigation to save or quit.

## Edit a file

From the repository root, open a disposable file in an interactive terminal:

```sh
cargo run -p kara -- scratch.txt
```

Press `i` to type, Esc to return to navigation, Ctrl-S to save, and Ctrl-Q to quit.
Saving overwrites the named file, and there is no undo.

## Arguments

| Argument | Meaning |
| --- | --- |
| `FILE` | Optional first argument: file to edit. Omit it for an unnamed buffer. |
| Further arguments | Ignored. There is no help/version option parser; `--help` is treated as a filename. |

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

Do not use `w other-file` on an already named buffer: it truncates `other-file`
without saving there, then writes to the original file. Command-bar filenames
cannot contain spaces. Saving adds line terminators rather than preserving the
original line endings.

## Develop Kara

For editor changes, test navigation, typing, saving, quitting, and terminal
restoration in a PTY or real terminal. The startup E2E only checks failure when
there is no usable terminal; it does not cover editing.

Based on [Philipp Flenker's Hecto guide](https://philippflenker.com/hecto/).
[All projects](../../README.md)
