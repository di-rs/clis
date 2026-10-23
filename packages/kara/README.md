# Kara

Kara 0.2 is a small modal terminal text editor based on
[Philipp Flenker's Hecto guide](https://philippflenker.com/hecto/). It preserves
Kara's navigation keys and adds literal search and Rust syntax highlighting.

## Edit a file

From the repository root, open an existing UTF-8 file in an interactive terminal:

```sh
cargo run -p kara -- scratch.txt
```

For a new file, omit the filename. Press `i` to type, Esc to return to navigation,
and Ctrl-S to save. An unnamed buffer opens a `:w ` prompt; enter its filename and
press Enter. Ctrl-Q quits, with protection for unsaved edits. There is no undo.

## Arguments

| Argument | Meaning |
| --- | --- |
| `FILE` | Optional file to edit. Omit it for an unnamed buffer. |
| `-h, --help` | Show help without opening a terminal. |
| `-V, --version` | Show version without opening a terminal. |
| `--` | End option parsing for a filename beginning with `-`. |

Extra operands and invalid arguments exit with status 2; startup or event-loop
failures exit with status 1. Normal quit exits with status 0. An unreadable or
invalid UTF-8 file reports an error inside the editor and leaves an unnamed buffer.

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
| Search | `/` forward; `?` backward; `n` repeat; `N` reverse in navigation |
| Enter a command | `:` from navigation, then Enter to run it |
| Quit | Ctrl-Q; press again consecutively to discard unsaved changes |

Commands include `w` to save, `w filename` to name an unnamed buffer, `q` to
request quit, and `q!` to discard changes and quit.

## Search

In navigation mode, `/` searches forward and `?` searches backward. Typing searches
incrementally from the original caret, wraps, and centers the selected result.
Both keys remain ordinary text in editing mode.

- Down/Right selects the next result; Up/Left selects the previous result.
- Enter accepts the query and location. Enter on an empty prompt repeats the
  previously accepted query in the prompt's direction, when one exists.
- Esc in a prompt restores the previous cursor, exact viewport, and accepted search.
- `n` repeats the accepted search direction; `N` reverses it. Both start from the
  current caret and wrap.
- Esc in navigation clears highlights but retains the query; repetition restores them.

Search is literal, case-sensitive, single-line, and non-overlapping. Empty live
queries show no results. Matches must start and end at complete grapheme boundaries:
`e` does not match the base of `é`, and an emoji component does not match part of
a joined emoji sequence. Search does not normalize Unicode text.

Visible matches use cyan backgrounds; the active match uses bold text on yellow.
Accepted highlights persist, and edits refresh matches against current text.

## Syntax highlighting

The status bar reports `Rust` for `.rs` files (case-insensitive) and `Text` otherwise.
Plain-text files have no syntax colors. A successful Save As updates the type and
colors, including a committed save with a durability warning. A failed save keeps
the previous type.

| Token | Color | Coverage |
| --- | --- | --- |
| Numbers | Red | Decimal integers, floats, scientific notation, binary, octal, hexadecimal |
| Keywords | Blue | Rust keywords and reserved words |
| Types | Green | Primitive types and common types such as `String`, `Vec`, `Option`, `Result` |
| Literal names | Purple | `Some`, `None`, `Ok`, `Err`, `true`, `false` |
| Characters | Amber | Quoted forms, including escaped quotes and backslashes |
| Lifetimes | Teal | ASCII letters, digits, and underscores after a quote |
| Comments | Dark green | Line comments and nested block comments across lines |
| Strings | Orange | Escaped quotes and strings spanning lines |

Numbers allow interior underscore separators, signed exponents, and uppercase
radix prefixes. Numeric fragments inside identifiers are not highlighted.
Comment delimiters inside strings remain string text; quotes inside comments
remain comment text. Search styling takes priority over syntax styling.

The lexer follows the guide's token coverage and does not validate Rust syntax.
When a later quote can start another lifetime or a complete single-character
literal, that boundary takes precedence over permissive coloring of invalid
quoted forms.
Raw-string parsing and other appendix features are outside this release. Kara has
no undo/redo, recovery journal, regular-expression search, or selection mode.

## Files and saving

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

## Install and verify

From the repository root, `make install-rust` installs Biggie, Parsu, Kara, and
Shish into `~/.local/bin` (override with `PREFIX=/path`). Add that directory to
`PATH`. Run `kara --help` from any directory. Kara requires no authentication.

For development, use the workspace's configured Rust toolchain and run affected
checks directly:

```sh
cargo fmt -p kara -- --check
cargo nextest run -p kara --profile ci
cargo clippy -p kara --all-targets --all-features -- -D warnings
cargo clippy -p kara --release --all-targets --all-features -- -D warnings
cargo build -p kara --release
uv run ruff check packages/kara/tests
uv run ruff format --check packages/kara/tests
uv run mypy packages/kara/tests
uv run cli-check -p kara
```

The nextest suite includes executable PTY regressions for modes, movement, Unicode
editing, search, file bytes, permissions, and terminal restoration. TestBackend
checks styles, cursor placement, clipping, and tiny/resized frames. Internal tests
cover match coordinates, lexer state, and annotation-cache invalidation. Linux and
macOS verification run in CI; the permission fixtures require Unix sockets and ACLs.

The screen uses Ratatui with Crossterm; document text lives in Ropey. Tabs retain
Kara's one-cell display. Terminal controls in file content and filenames are
rendered safely. Crossterm honors `NO_COLOR` when set; color-specific PTY tests
explicitly remove it from their child environment.

[Architecture](docs/architecture.md) describes ownership and interfaces.
[Release verification](docs/release-0.2.0.md) records the dependency review and build sizes.

[All projects](../../README.md)
