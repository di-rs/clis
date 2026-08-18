# line_prefixer

Crate to add prefix to the lines when it used with other writers.
A small `std::io::Write` adapter that prefixes non-empty lines.

It handles:

* multiple lines per `write()`
* lines split across multiple `write()` calls
* arbitrary bytes
* buffered incomplete lines, flushed by `flush()`

## Usage

```rust
use line_prefixer::PrefixWriter;

let stdout = std::io::stdout();
let mut writer = PrefixWriter::new(stdout.lock(), "prefix: ");

writeln!(writer, "hello")?;
writeln!(writer, "world")?;
writer.flush()?;
```

Output:

```text
prefix: hello
prefix: world
```

## Shoutout

It replicates functionality from the [prefix_writer](https://github.com/AlexanderThaller/prefix_writer) crate, but in a way I like it to do things.
