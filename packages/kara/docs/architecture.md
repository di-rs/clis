# Kara architecture

Kara is a single-process terminal editor. Its entry point initializes logging and
parses the optional filename before terminal setup. The existing command-line
interface is unchanged in 0.2.

## Ownership and modules

| Boundary | Modules | Responsibility |
| --- | --- | --- |
| Runtime | [editor](../src/editor.rs), [terminal](../src/editor/terminal.rs) | Event loop, terminal initialization, cursor shape, teardown |
| Application/input | [state](../src/editor/state.rs), [keymap](../src/editor/keymap.rs), [command](../src/editor/command.rs), [prompt](../src/editor/prompt.rs) | Modes, bindings, dispatch, reusable prompts |
| Document | [document](../src/editor/document.rs), [buffer](../src/editor/buffer.rs) | One Ropey text store, file identity, revision, modified state |
| Position/layout | [cursor](../src/editor/cursor.rs), [view](../src/editor/view.rs), [line layout](../src/editor/line_layout.rs) | Grapheme movement, terminal-cell scrolling, horizontal clipping |
| Persistence | [persistence](../src/editor/persistence.rs) | UTF-8 loading, atomic saving, permissions and durability |
| Rendering | [UI](../src/editor/ui.rs) | Ratatui document widget and standard widgets for bars |
| Search | [search](../src/editor/search.rs) | Literal matches, direction, revision cache, prompt snapshots |
| Highlighting | [cache and composition](../src/editor/highlight.rs), [lexer](../src/editor/highlight/lexer.rs) | Byte annotations, line state, incremental invalidation, style priority |
| File type | [file type](../src/editor/file_type.rs) | Rust/plain-text detection from committed file identity |

## Text and coordinates

The document owns one rope. Editing translates a logical line/grapheme position
into a Unicode scalar offset. Individual rope lines are materialized for grapheme
segmentation and lexing; edits and frames do not rebuild a whole-document string.
Line layouts are derived display data, not a second text store.

Byte offsets identify lexer annotations and search candidates. Grapheme indexes
identify text positions, and terminal cells identify viewport offsets. Layout
retains source byte/grapheme indexes through clipping, including partial wide
characters and replacement glyphs for controls. Ratatui handles frame diffing;
Kara retains its editing and layout logic.

The rope recognizes LF and CRLF. Loading and saving preserve UTF-8 bytes, mixed
endings, and missing final newlines. New breaks use the first convention found,
defaulting to LF. Empty documents have an editable position; a final newline
exposes a final empty line.

## Search and highlighting

Search scans individual rope lines for case-sensitive, non-overlapping matches
whose endpoints are complete grapheme boundaries. Cached matches are tied to the
document revision. A prompt snapshots the prior cursor, viewport, and accepted
search; cancellation restores them together. Repetition uses the current caret
and accepted direction. Clearing highlights leaves the accepted query available.

The syntax cache stores annotations and outgoing lexer state for each line, not
source strings. Each edit records the affected line and aligns cached rows when
lines split or join. Recalculation starts at the first affected line, processes
all directly changed lines, and stops once the outgoing state stabilizes. That
state carries nested block-comment depth and whether a multiline string is open.
Search styling takes precedence when annotations are composed for visible cells.

File type is derived from the active filename. A committed Save As changes it;
a failed save cannot accidentally enable another file type's colors.

## Persistence and terminal lifecycle

Persistence functions receive text and a path explicitly. Saving streams rope
chunks into a temporary file inside a private staging directory, restores and
verifies destination access metadata, then syncs and atomically replaces the
destination. Results distinguish failure before replacement from a committed
save whose directory sync failed. Both committed outcomes update identity and
clear modified state; a durability warning remains visible for the latter.

Terminal initialization rolls back on failure. Teardown attempts every restoration
step, including raw mode, even if another step fails. Drawing and event-loop
errors propagate to the process boundary. Filename, title, and message text is
sanitized before display. See the [README](../README.md#files-and-saving) for the
supported-platform and metadata-preservation limits.

## Diagnostics and verification

[Startup](../src/main.rs) disables console logging so diagnostics do not overwrite
the screen; [cli-log](../../../utils/cli-log/README.md) records file logs. Handled
open/save failures remain observable without interrupting the editor.

Executable PTY tests establish modal behavior, file contents, permission failures,
and terminal restoration. Ratatui TestBackend checks visible cells, styles, cursor
placement, clipping, and resized frames. Focused internal tests cover Unicode
coordinates, rope chunk boundaries, multiline lexer state, and cache stabilization.
The [release record](release-0.2.0.md) documents the final validation scope.
