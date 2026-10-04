# Kara

Kara is a small terminal text editor with navigation, editing, and command modes.

## Run

From the workspace root, in an interactive terminal:

```sh
cargo run -p kara
```

To open an existing file, pass its path after `--`; saving
changes overwrites it.

## Basic controls

- Start in navigation mode; arrow keys and vim-like motions.
- Press `i` to edit, then type text, Enter, Tab, Backspace, or Delete.
- Press Esc or Ctrl-C to return to navigation mode.
- Ctrl-S saves; an unnamed buffer prompts for a filename via the command bar.
- From navigation mode, `:` enters command mode. Type `w filename` to name and
  save an unnamed buffer, `w` to save the current file, `q` to request quit, or
  `q!` to discard changes and quit; press Enter to run the command.
- Ctrl-Q quits; with unsaved changes, a second consecutive Ctrl-Q discards them.

## Limits and credit

There is no undo command. Command-bar filenames cannot contain spaces, and saving
writes newline-terminated lines rather than preserving the original line endings.
Do not use `w other-file` on an already named buffer: it truncates `other-file`
without saving into it, then writes to the original file instead. Use disposable
files when experimenting. Kara has no direct GNU Coreutils counterpart.

Tutorial credit: [Philipp Flenker's Hecto guide](https://philippflenker.com/hecto/).

[Workspace README](../README.md)
