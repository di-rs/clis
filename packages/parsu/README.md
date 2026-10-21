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
