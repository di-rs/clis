# Shish architecture

The imported shell contains a prompt, token processing, external command lookup,
and process execution. The binary accepts a command string or reads stdin.
The private `syntax` module uses published `brush-parser` 0.4.0 for `-n` checks,
returning an AST or a diagnostic with a source position (or end-of-input marker).
It cannot execute commands. Normal execution still uses the imported evaluator
until the next foundation PR replaces it.

The source was imported from
[di-rs/shish](https://github.com/di-rs/shish/tree/46c41468d4105a238c439aa68b4ff0e3248a964e)
without its utility crates or Git history. Changes in this import adapt package
layout, lint requirements, and a command-string entry point for reproducible tests.
The root Cargo lockfile records dependency versions; there is no vendored fork.

## Runtime selection

[Brush](https://github.com/reubeno/brush) provides separable parser, runtime,
builtin, and interactive libraries. Shish will delegate runtime parsing,
expansion, shell state, and execution to those libraries, retaining only CLI and
Sanctuary utility integration. Keeping that integration in this package avoids a
shared API before another component needs one.

[Flash](https://github.com/raphamorim/flash) is another Rust parser and interpreter,
but Brush's existing interactive shell and extensible builtin registry fit this
migration more directly. Brush uses the MIT license; Flash uses GPL-3.0. This
choice does not establish POSIX conformance: upstream compatibility claims and
limitations still require reproducing against the integrated shish binary.

Shish configures sh/POSIX mode by default. The parser facade is for syntax
inspection; its AST is neither a second evaluator nor a durable execution plan.
Runtime replay and recovery require explicit contracts outside shell syntax.

Shish itself supplies neither isolation nor crash recovery. Those contracts belong
to Boxie and the future durable-execution design.

