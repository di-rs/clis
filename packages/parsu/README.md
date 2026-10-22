# Parsu

Parsu reads a small XML-like language and prints its element tree using Rust
debug formatting. It is a parser-combinator exercise, not a general XML parser.

## Parse an element

Run from the repository root with the pinned Rust toolchain installed:

```sh
printf '%s\n' '<parent><child/></parent>' | cargo run -p parsu -- -
```

The output is a debug representation of the parent element and its child.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `FILE` | Required UTF-8 file, or `-` for non-terminal stdin. |
| `--json` | Emit a JSON tree or error envelope. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

## Accepted syntax

Nested elements, self-closing elements, and double-quoted attributes are supported.
Names start with a letter and continue with letters, numbers, or hyphens. Closing
names must match, and input must contain exactly one root.

`<child/>` works, but `<child />` does not: whitespace before a closing bracket
is rejected. Surrounding whitespace and whitespace inside empty parents are
allowed. Additional roots or trailing non-whitespace input are errors.

Text nodes, namespaces, comments, entities, escaped quotes, and schema validation
are not supported.

Based on Bodil Stokke's [Learning Parser Combinators With Rust](https://bodil.lol/parser-combinators/).
[Architecture](docs/architecture.md) explains the implementation and boundaries.

[All projects](../../README.md)

## Installation and machine-readable output

From the repository root, `make install-rust` installs Biggie, Parsu, and Kara in
`~/.local/bin`; add it to `PATH`. Parsu runs from any directory without network
access or authentication. Keep the existing debug output for human inspection;
use `--json` for scripts:

```sh
printf '<root a="b"/>' | parsu --json -
```

```json
{"ok":true,"data":{"name":"root","attributes":[["a","b"]],"children":[]}}
```

Attributes are ordered name/value pairs; children retain source order. This is a
CLI envelope, not an external API response. Errors use
`{"ok":false,"error":{"kind":"operation","message":"..."}}`; invalid arguments
use kind `arguments`. Statuses are 0 for success, 1 for operation failure, and 2
for invalid arguments. JSON goes to stdout, diagnostics to stderr; help/version
remain text. Input contents are not included in parse errors. The reported byte
offset identifies the unparsed remainder, not necessarily the deepest grammar
failure because combinators can backtrack. The complete input/tree still reside
in memory; this change does not expand the grammar or impose input/depth limits.
