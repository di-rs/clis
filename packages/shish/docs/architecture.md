# Shish architecture

Shish embeds Brush in a single application package. The CLI selects input and
shell settings; Brush owns runtime parsing, expansion, command lookup, variables,
builtins, pipelines, redirections, process execution, and jobs. The terminal loop
uses `brush-interactive` with its Reedline backend. No second evaluator remains.

The root Cargo lockfile records published `brush-parser` 0.4.0, `brush-core` 0.5.0,
`brush-builtins` 0.2.0, and `brush-interactive` 0.4.0. There is no fork or vendored
patch. The private `syntax` module provides parse-only inspection, returning an
AST or a located diagnostic without evaluation. Runtime execution uses Brush's
own parsing APIs.

## Input and execution boundary

Command strings and scripts use Brush's corresponding execution APIs. For stdin,
shish accumulates lines until Brush reports a complete command, then asks Brush
to execute that text. An unbuffered stdin descriptor and a one-byte input buffer
prevent reading ahead into data intended for `read` or external programs.
Incomplete input at EOF is passed to Brush for a syntax diagnostic. This adapter
handles input delivery only; it does not interpret shell AST nodes.

Shish preserves the last status and delegates exit handling and traps to Brush.
A Tokio runtime supports asynchronous execution and the blocking terminal editor.
Startup configuration files are skipped intentionally; see the
[invocation reference](../README.md#run-commands). The default sh/POSIX flags do
not establish conformance or disable every Brush extension.

## Runtime selection

[Brush](https://github.com/reubeno/brush) provides separable parser, runtime,
builtin, and interactive libraries. Its existing interactive shell and extensible
builtin registry fit the migration more directly than
[Flash](https://github.com/raphamorim/flash), another Rust parser and interpreter.
Brush uses the MIT license; Flash uses GPL-3.0. Brush also requires transitive
dependencies under other licenses. The repository policy has exact-version
exceptions for `check_elevation` 0.2.7 (BSD-2-Clause), `foldhash` 0.2.0 (Zlib), and
`terminfo` 0.9.0 (WTFPL), with their [notices](../THIRD-PARTY-NOTICES.md).
Dependency updates must recheck these exceptions. Upstream compatibility claims and
reported limitations still require reproduction against the integrated binary.

Keeping integration in this package avoids introducing a shared API before
another component needs one. A shell AST is neither a second evaluator nor a
durable execution plan: recovery requires explicit contracts outside syntax.
Shish supplies neither isolation nor crash recovery. Those responsibilities belong
to Boxie and the future durable-execution design.

## Migration provenance

The initial shell was imported from
[di-rs/shish](https://github.com/di-rs/shish/tree/46c41468d4105a238c439aa68b4ff0e3248a964e)
without its utility crates or Git history. The original evaluator, expansion,
prompt loop, and `target/debug` command discovery have now been removed.
