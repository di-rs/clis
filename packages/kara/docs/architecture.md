# Kara architecture

Kara is a single-process terminal editor. Its entry point initializes logging,
selects the optional filename, and constructs an editor. The editor owns the event
loop and terminal lifecycle; there is no service or database behind it.

## Input, state, and rendering

[The editor](../src/editor.rs) translates terminal events into commands according
to the current mode. Its view and buffer hold document and cursor state, while
command, status, and message bars provide separate parts of the screen.
The terminal adapter contains terminal I/O. Editor teardown restores the terminal.

File loading and saving operate on local files. Unsaved edits live in memory;
there is no recovery journal or undo system. The [README](../README.md) documents
controls and current saving limitations, which matter before using real files.

## Diagnostics and verification

[Startup](../src/main.rs) disables console logging so diagnostics do not overwrite
the editor display, while [cli-log](../../../utils/cli-log/README.md) records file
logs. Startup errors propagate to the process boundary.

Interactive behavior needs a PTY or real terminal: navigation, editing, saving,
quitting, and restoration cannot be established by the non-terminal startup test.
