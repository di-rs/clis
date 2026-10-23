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
| Move | Arrow keys; `u` up, `j` down, `h` left, `k` right in navigation mode |
| Move within a line | `s` to the first non-whitespace grapheme; `e` to the end |
| Move within the file | `g` to the first line; `G` to the last; `p`/`P` half-screen up/down |
| Start editing | `i` at the caret; `a` after it; `I` at the first non-whitespace grapheme; `A` at line end |
| Return to navigation | Esc or Ctrl-C |
| Delete | `d` in navigation; Delete or Backspace while editing |
| Save | Ctrl-S; unnamed buffers prompt for a filename |
| Enter a command | `:` from navigation, then Enter to run it |
| Quit | Ctrl-Q; press again consecutively to discard unsaved changes |

Commands include `w` to save, `w filename` to name an unnamed buffer, `q` to
request quit, and `q!` to discard changes and quit.

## Known limitations

`w other-file` saves to that destination and makes it the active file only after
success. Command-bar filenames may contain spaces; everything after the first
space following `w` is the literal filename, without shell quoting or expansion.
Failures before replacement leave the buffer identity and modified state intact.
If replacement succeeds but syncing its directory fails, Kara switches to the saved
file, clears the modified state, and warns that the file was saved but directory
sync failed. The new contents are visible, but may not survive a crash or power loss.

Saving uses a temporary file inside a private staging directory beside the
destination, followed by an atomic rename.
Existing symlinks with valid targets are resolved. On Linux and macOS, existing
owner, group, mode bits, and access ACLs are preserved and verified before replacement.
Kara checks write access to the destination and rejects saves when it cannot preserve
that access metadata. The staging directory's inherited access ACL is removed before
creating the temporary file; its contents stay private while permissions are restored.
Other platforms reject saves until ACL preservation is supported.
Dangling symlinks are rejected without replacing the link or creating its target.
Read-only destinations and existing non-regular files (including FIFOs, sockets,
and devices) are rejected. On Unix, the parent directory is synced
after replacement.
Replacement breaks hard-link identity and does not preserve all extended metadata.
New files are private to their owner. Saving preserves UTF-8 bytes, existing LF
and CRLF endings (including mixed endings), and whether the file ends in a newline.
Enter inserts the first line-ending convention found in the file, or LF if none
exists. A trailing newline exposes an empty final line; `G` moves to that line.
Files containing invalid UTF-8 are rejected without changing their contents.

## Develop Kara

The screen uses Ratatui with the Crossterm backend. Kara keeps its own modal
editing and Unicode layout logic; resizing also supports very small terminals.

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

The CLI tests include the PTY save regressions. Run them separately from the
repository root after building Kara:

```sh
python3 packages/kara/tests/pty_smoke.py "$PWD/target/debug/kara"
```

`/` in navigation mode opens a search prompt. Type a query, use Backspace to
remove a grapheme, and press Enter or Esc to return to navigation. Typing searches incrementally and moves to the first literal, case-sensitive match at or after the starting caret,
wrapping to the beginning when needed. Down/Right selects the next result; Up/Left selects the previous result,
using the same non-overlapping match order in either direction. Results
are centered in the viewport.
Enter accepts the location; Esc restores the original cursor and exact scrolling. Matching is
single-line and non-overlapping; an empty query has no matches. In editing mode, `/` is ordinary text.
