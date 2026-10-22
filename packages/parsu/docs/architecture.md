# Parsu architecture

Parsu separates file and terminal I/O from a parser-combinator library. It parses
a restricted XML-like language into an in-memory element tree; it is not a general
XML processor.

## Parsing flow

[The CLI](../src/main.rs) reads a complete UTF-8 input string from a file or
non-terminal stdin, calls `parse_xml`, and writes the resulting tree using debug
formatting by default or a serde JSON envelope with `--json`. It owns buffering,
anyhow diagnostic context, and flushing. Errors report the byte offset of the
unparsed remainder without copying input contents into diagnostics.

[The parser abstraction](../src/parser.rs) returns a parsed value and the remaining
input, or an input slice identifying failure. [Combinators](../src/combinators.rs)
compose sequencing, alternatives, repetition, predicates, and transformations.
[The grammar](../src/lib.rs) builds elements from names, attributes, and recursively
nested children, checks matching closing names, and rejects trailing input.

## Boundaries

The parser works on borrowed input slices while constructing owned element values.
The CLI reads the whole input rather than streaming elements. It does not implement
namespaces, entity expansion, or schema validation. The [README](../README.md)
contains the accepted syntax, limitations, example, and original tutorial credit.

Grammar and combinator tests live with the library; CLI tests exercise the process
boundary. Logging uses [cli-log](../../../utils/cli-log/README.md).
