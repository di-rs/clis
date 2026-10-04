# parsu

`parsu` parses a small XML-like language and prints the resulting element tree using Rust debug formatting.

## Supported capabilities

- Read one named UTF-8 file, or `-` for non-terminal stdin.
- Parse nested elements, self-closing elements, and double-quoted attributes.
- Names start with a letter, followed by letters, numbers, or hyphens.
- Closing element names must match their opening names.

## Example

From the workspace root:

```sh
printf '<parent><child name="demo"/></parent>\n' | cargo run -p parsu -- -
```

## Limits and credit

This is not a general XML parser: there are no text nodes, namespaces, comments,
entity decoding, escaped quotes, or schema validation. Whitespace before `>` or
`/>` is rejected (`<child/>` works; `<child />` does not). Input must contain
exactly one root element, with optional surrounding whitespace; trailing non-whitespace
input and additional roots are rejected. Empty parents may contain whitespace.

There is no direct GNU Coreutils counterpart. Based on Bodil Stokke's tutorial,
[Learning Parser Combinators With Rust](https://bodil.lol/parser-combinators/).

[Workspace README](../README.md)
