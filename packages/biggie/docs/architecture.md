# Biggie architecture

Biggie separates command-line and filesystem handling from streaming generators.
The binary parses and validates arguments, optionally emits a preview, chooses outputs, and dispatches to the
library. Library callers supply typed options and writers directly.

## Generation flow

[CLI parsing](../src/cli.rs) translates subcommands into operation-specific options.
[The binary](../src/main.rs) selects a generator; [output handling](../src/output.rs)
opens buffered destinations, checks conflicting file identities, and flushes output.
The [library](../src/lib.rs) exports generators for text, records, fields, bytes,
and sorted pairs. Shared helpers supply random sources and line endings.

Generators validate their options before writing and stream through caller-owned
writers. They do not flush those writers. File opening and final flushing belong
to the CLI layer, so write errors can leave partial output. The pair operation
uses two destinations and checks their identities before generation.

## Reuse and boundaries

The benchmark runner invokes Biggie as a process; generation does not depend on
the benchmark runner or a sandbox service. CSV support is a Cargo feature, while
other field generation remains available without it. Seeded random generation
uses a shared RNG helper; the reproducibility scope is documented in the README.

CLI lifecycle logging uses [cli-log](../../../utils/cli-log/README.md). The library
returns errors to its caller and leaves command diagnostics to the binary.

See the [README](../README.md) for commands, options, examples, output semantics,
and library usage. Operation-module doctests and package tests cover generation;
CLI tests cover argument and filesystem behavior.
