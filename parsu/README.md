 # Practicing Rust by building parsing combinators for simplified XML

https://bodil.lol/parser-combinators/

## Statement

We're going to write a parser for a simplified version of XML. It looks like this:

```xml
<parent-element>
  <single-element attribute="value" />
</parent-element>
````

XML elements open with the symbol < and an identifier consisting of a letter followed by any number of letters, numbers and -. This is followed by some whitespace, and an optional list of attribute pairs: another identifier as defined previously, followed by a = and a double quoted string. Finally, there is either a closing /> to signify a single element with no children, or a > to signify there is a sequence of child elements following, and finally a closing tag starting with </, followed by an identifier which must match the opening tag, and a final >.

That's all we're going to support. No namespaces, no text nodes, none of the rest, and definitely no schema validation. We're not even going to bother supporting escape quotes for those strings - they start at the first double quote and they end at the next one, and that's it. If you want double quotes inside your actual strings, you can take your unreasonable demands somewhere else.
