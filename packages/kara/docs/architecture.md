# Kara architecture

Kara is a single-process terminal editor. Its entry point initializes logging,
uses clap to parse help, version, and the optional filename before terminal setup, and constructs an editor. The editor owns the event
loop and terminal lifecycle; there is no service or database behind it.

## Input, state, and rendering

[The editor](../src/editor.rs) owns the terminal session and event loop.
The keymap translates events into commands according to the current mode;
application state dispatches those commands without initializing a terminal.
The document owns a Ropey text buffer, file identity, and modified state. A separate cursor
implements movement in logical lines and graphemes; the viewport scrolls in
terminal cells. Distinct text and screen position types keep those coordinates
from being exchanged accidentally. Line layout converts graphemes to display
cells. Layout is derived from an individual rope line when needed; it is not a
second document text store. Rope edits translate grapheme positions to character
offsets, so editing does not rebuild the entire document.
Ratatui owns frame diffing and draws status, command, and message bars with standard
widgets. A small document widget applies Unicode cell clipping before rendering.
The viewport owns scrolling; the document retains all editing behavior. The
Crossterm adapter manages terminal setup and cursor shape. Drawing errors propagate
to the process boundary, and editor teardown restores the terminal.

Persistence streams UTF-8 into and out of the rope without normalizing line
endings or constructing a second whole-document string. Ropey's LF/CRLF line
model includes an editable empty document and an empty line after a final newline.
Persistence functions accept text and a path explicitly; there is no nested
file-buffer wrapper or implicit dereferencing between storage layers.
File loading and saving operate on local files. Command parsing does not open files.
Saving writes a temporary file inside a private directory, restores and verifies the destination's
access metadata, then syncs and replaces the destination. The save result separates
failure before replacement from a committed save whose directory sync failed.
Both committed outcomes update buffer identity; the latter displays a durability
warning. Terminal initialization rolls
back on failure; teardown attempts every restoration step, including raw mode. Unsaved edits live in memory;
there is no recovery journal or undo system. The [README](../README.md) documents
controls and current saving limitations, which matter before using real files.

## Diagnostics and verification

[Startup](../src/main.rs) disables console logging so diagnostics do not overwrite
the editor display, while [cli-log](../../../utils/cli-log/README.md) records file
logs. Startup errors propagate to the process boundary.

Interactive behavior needs a PTY or real terminal: navigation, editing, saving,
quitting, and restoration cannot be established by the non-terminal startup test.

Ratatui TestBackend checks rendered Unicode cells, clipping, status styles, cursor
placement, and tiny/resized terminals without initializing a real terminal.
