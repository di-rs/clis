# Shish architecture

The imported shell contains a prompt, token processing, external command lookup,
and process execution. The binary accepts a command string or runs the original
prompt loop. Parser and executor modules remain private to this package.

The source was imported from
[di-rs/shish](https://github.com/di-rs/shish/tree/46c41468d4105a238c439aa68b4ff0e3248a964e)
without its utility crates or Git history. Changes in this import adapt package
layout, lint requirements, and a command-string entry point for reproducible tests.
Subsequent PRs replace the parser and runtime with Brush.

Shish itself supplies neither isolation nor crash recovery. Those contracts belong
to Boxie and the future durable-execution design.

