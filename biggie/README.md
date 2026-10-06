# Biggie

`biggie` generates reproducible text, literal records, delimited data, raw bytes,
and sorted input pairs for CLI testing, benchmarks and general synthetic datasets.

## Cargo features

`csv` is the only optional feature and is enabled by default to preserve the full
CLI. It enables the optional `csv` dependency, CSV encoding and `FieldFormat::Csv`.
To build Biggie without that dependency:

```sh
cargo build --locked --release -p biggie --no-default-features
cargo run --locked -p biggie --no-default-features -- fields -n 1 -v a -v b -v c -
```

All five commands remain available, including plain-delimited `fields`. A build
without CSV lists only `delimited` in format help and rejects `--format csv` with
exit 2 before creating or truncating output. To enable CSV explicitly:

```sh
cargo build --locked --release -p biggie --no-default-features --features csv
```

For a Rust consumer beside this checkout:

```toml
biggie = { path = "../clis/biggie", default-features = false }
```

Add `features = ["csv"]` to that dependency when needed. `FieldFormat::Csv` is
available only with the feature; the other types and operations remain available.
These are compile-time choices, not runtime flags. A full binary includes CSV
support regardless of which command is selected. See [Cargo features](https://doc.rust-lang.org/cargo/reference/features.html).

CSV is the useful data-format feature boundary: `rand` supports text, random
fields/bytes and seeded pairs, while the other runtime dependencies support the
CLI or shared diagnostics. No other feature gates are added. A separate library-only
CLI-dependency feature could be useful later for a concrete embedding requirement.

`cargo tree --locked -p biggie --no-default-features --edges normal,build` verifies
the selected Biggie dependency graph has no `csv` or `csv-core`. They still appear
in the workspace lockfile because other apps such as `cutr` use them. Cargo features
are additive: another consumer enabling Biggie's `csv` feature in the same build
can enable it for all uses of Biggie.

Verify both configurations when changing Biggie:

```sh
cargo nextest run --locked -p biggie
cargo nextest run --locked -p biggie --no-default-features
cargo nextest run --locked -p biggie --no-default-features --features csv
cargo test --locked -p biggie --no-default-features --doc
cargo clippy --locked -p biggie --no-default-features --all-targets
```

## Commands and output

Run examples from the workspace root. Use disposable output paths: named output
files are created or truncated. Single-stream commands default to `out.txt`;
`-` sends only generated data to stdout, while `./-` names a literal file.

```sh
cargo run --locked -p biggie -- -s 42 -n 100 biggie-example.txt
cargo run --locked -p biggie -- text -s 42 -n 100 biggie-example.txt
cargo run --locked -p biggie -- records -r miss -r Hit -r '' -p 2 -c 2 -
cargo run --locked -p biggie -- fields -n 2 -F csv -d , -v 'a,b' -v '' -v 'a"b' -
cargo run --locked -p biggie -- bytes -b 8193 -p 00ff1b0d0a boundary.bin
cargo run --locked -p biggie -- pair -l left.txt -r right.txt -a 2 -j 3 -b 1 -c 2
```

Omitting a command selects `text`, with the same options and defaults as explicit
`biggie text`. Command-specific flags follow an explicit command. Root text flags
cannot be combined with a subcommand. `-h/--help` lists commands/default text
options; `biggie COMMAND --help` shows that command's options. `-V/--version`
works at root and for each command.

**CLI migration:** `text`, `records`, `fields`, and `bytes` are now command names,
along with `pair` and Clap's `help`. To write text to a file named `records`, use
`biggie text records`, `biggie ./records`, or `biggie -- records`. Existing text
flags and the default generated ASCII data are preserved.

Every flag has a short alias. Aliases are local to their command, so `-l` means
word length in text/fields and the left destination in pair. Positional `FILE`
is an operand. All commands accept `-L/--log-level LEVEL` before or after the
command: `off`, `error`, `warn`, `info`, `debug`, `trace`. Explicit level overrides
`CLIS_LOG_LEVEL`, which overrides off, even if the overridden variable is invalid.
`-v` is a field value in `fields`, not a verbosity switch. There are no logging
file/JSON flags or separate timing switch; see [observability](../docs/observability.md).

## Text

| Short | Long | Meaning and default |
| --- | --- | --- |
| `-n` | `--lines N` | u64 record count, default 100,000; zero emits empty output. |
| `-s` | `--seed U64` | Seed, including zero; omitted means fresh randomness. |
| `-w` | `--words-per-line RANGE` | Default `7..=14`; zero permits blank records. |
| `-l` | `--word-length RANGE` | Positive units per word, default `2..=11`. |
| `-a` | `--alphabet ascii\|unicode` | Default ASCII alphanumeric; conflicts with explicit custom alphabet. |
| `-A` | `--alphabet-chars STRING` | 1–4096 distinct literal scalars; no CR/LF or duplicates. |
| `-u` | `--length-unit bytes\|scalars` | Default bytes; non-ASCII alphabets require scalars. |
| `-d` | `--word-separator STRING` | Default one space; empty, tabs and Unicode accepted; CR/LF rejected. |
| `-e` | `--line-ending lf\|crlf` | Default LF, independent of host OS. |
| `-N` | `--no-final-newline` | Omit only the final record terminator. |

Ranges are fixed `N` or inclusive `MIN..=MAX` using u32 values. Open-ended,
exclusive or descending ranges are rejected. Inclusive ranges and alphabet
members are sampled uniformly. The Unicode preset is ASCII letters/digits plus
é, β, 猫, 界, U+0301 combining acute, U+1F642 smiling face and U+200D zero-width
joiner. There is no normalization, grapheme counting or display-width counting.
For meaningful multi-scalar sequences, use literal records. Separators are exact
content and need not be whitespace.

```sh
cargo run --locked -p biggie -- text -A 猫 -u scalars -n 2 -w 2 -l 3 -
```

A record count is not a newline count: three blank records without a final
terminator produce two terminators; one blank unterminated record emits no bytes.
Variable text lengths do not specify an exact file size. Text domain scratch memory is
an 8 KiB buffer plus a bounded alphabet; it does not grow with word/record length.

## Literal records

| Short | Long | Meaning and default |
| --- | --- | --- |
| `-r` | `--record STRING` | Repeatable literal records, including empty; embedded LF rejected. |
| `-f` | `--records-file PATH` | UTF-8 LF-delimited corpus; conflicts with literal records. |
| `-p` | `--repeat N` | Positive u64 copies of each adjacent record, default 1. |
| `-c` | `--cycles N` | u64 full schedule cycles, default 1; zero is valid. |
| `-e` | `--line-ending lf\|crlf` | Default LF. |
| `-N` | `--no-final-newline` | Omit only the last record terminator. |

Emit each record `repeat` times, then repeat the entire schedule `cycles` times.
An empty schedule emits nothing. Adjacent equal records, including across cycles,
merge into one longer observed duplicate run. The example above has 12 records;
literal `Hit` appears at positions 3, 4, 9 and 10 (one-based). Case variants are
explicit entries. Biggie does not synthesize regexes or certify arbitrary regex
match counts; evaluate those against the explicit corpus.

A trailing LF closes a record and adds no phantom record; CR remains literal
content. Empty files contain zero records. File bytes/literal content are limited
to 8 MiB and the corpus to 100,000 entries, including empty ones. Expanded counts
are checked for u64 overflow. Memory includes corpus content and entry metadata,
never the expanded output. Decode and validate the corpus before output creation;
input/output aliases, including existing symlinks/hard links, are rejected. No seed,
word-shape or `--lines` flags apply.

## Fields

| Short | Long | Meaning and default |
| --- | --- | --- |
| `-n` | `--lines N` | u64 logical record count, default 100,000; zero valid. |
| `-f` | `--fields N` | Positive u32 cells per record, default 3. |
| `-F` | `--format delimited\|csv` | Default plain-delimited text; CSV quotes/escapes cells. |
| `-d` | `--delimiter STRING` | One ASCII byte, default tab; NUL/CR/LF/quote rejected. |
| `-v` | `--field-value STRING` | Repeatable literal cells, cycled across rows. |
| `-E` | `--empty-every N` | Positive u64; every Nth cell is empty, counting from one across rows. |
| `-s` | `--seed U64` | Seed for random cells; conflicts with literal values. |
| `-l` | `--word-length RANGE` | Positive random ASCII cell length, default `2..=11`; explicit values conflict. |
| `-e` | `--line-ending lf\|crlf` | Default LF record terminator. |
| `-N` | `--no-final-newline` | Omit only the final record terminator. |

Without literal values, cells contain random ASCII alphanumerics; plain-delimited
generation excludes an alphanumeric delimiter from its cell alphabet. Empty-every
replaces a cell without removing its position. Leading/trailing empty fields
remain present. Plain-delimited values cannot contain the delimiter, CR or LF;
quotes are ordinary content. CSV doubles quotes and quotes cells when required,
including embedded delimiters/newlines. There are no headers. CSV record counts
are logical records, not physical line counts; embedded CR/LF is preserved even
without a final record terminator.

Validate a conservative maximum encoded row of 8 MiB (including worst-case quoting),
literal content of 8 MiB and total cell-count overflow before output. Generation
retains one bounded encoded row and one bounded random cell. CSV flushes only the
internal row buffer. Current `cutr` CSV parsing differs from GNU cut's plain fields.

## Bytes

| Short | Long | Meaning and default |
| --- | --- | --- |
| `-b` | `--bytes N` | Required u64 exact decimal byte budget; zero valid. |
| `-p` | `--pattern-hex HEX` | Repeating nonempty even-length hex; at most 8 MiB decoded. |
| `-s` | `--seed U64` | Seed for random bytes; conflicts with a pattern. |

Without a pattern, generate uniformly sampled byte values 0–255. The budget may
end inside a pattern, UTF-8 sequence or CRLF. No terminator is added. Output can
include NUL, ANSI/control bytes and invalid UTF-8 and is identical in a terminal,
file or pipe. Text/record/ending flags are rejected. Scratch memory is 8 KiB plus
any supplied pattern. `tailr` preserves these bytes; `headr` and `cutr` currently
have documented lossy byte behavior, which these fixtures can expose.

## Sorted pairs

| Short | Long | Meaning and default |
| --- | --- | --- |
| `-l` | `--left FILE` | Required named left destination. |
| `-r` | `--right FILE` | Required named right destination. |
| `-a` | `--left-only N` | Required u64 count of distinct left-only keys. |
| `-j` | `--shared N` | Required u64 count of distinct shared keys. |
| `-b` | `--right-only N` | Required u64 count of distinct right-only keys. |
| `-c` | `--copies N` | Positive u64 multiplicity of every key, default 1. |
| `-s` | `--seed U64` | Seed for a shared random hex prefix; omitted prefix is `biggie`. |
| `-e` | `--line-ending lf\|crlf` | Default LF. |
| `-N` | `--no-final-newline` | Omit the final terminator independently in each output. |

Keys are `PREFIX-ORDINAL`, with zero-padded 16-digit lowercase hex ordinals.
Ascending blocks contain left-only, shared, then right-only keys. Left emits its
first two blocks; right emits the last two. Thus the example produces 10 left
records, 8 right records, 6 shared occurrences, 4 left-only and 2 right-only.
Generation uses constant scratch memory and requires no sort. Ordering is UTF-8
byte/Rust string order, not locale collation. This does not sort arbitrary text.

All counts may be zero; union and per-stream multiplication/addition are checked.
No positional output, stdout destination, or text-shape flags apply. Identical
paths and existing hard-link/symlink aliases are rejected. Files are opened without
truncation and their identities compared again before replacing contents. This
is not transactional: opening the second file can leave the first created, and
write/flush failure can leave either output partial. Both output buffers must
flush before success is reported.

## Rust library and errors

Each command has a separate top-level operation and typed options:

| Operation | Options | Caller-owned resources |
| --- | --- | --- |
| `generate_text` | `TextOptions` | One `Write` |
| `generate_records` | `RecordOptions` | One `Write` |
| `generate_fields` | `FieldOptions` | One `Write` |
| `generate_bytes` | `ByteOptions` | One `Write` |
| `generate_pair` | `PairOptions` | Two `Write` values |

All return `io::Result<()>`, validate direct callers before writing (also for zero
output), preserve I/O error causes and never flush the caller's writers. Wrap
files in `BufWriter` for efficient small writes. The single-stream CLI owns an
additional 64 KiB output buffer; pair owns one default buffer per destination. Domain modules do not parse argv,
use global streams, initialize diagnostics or exit the process. Random operations
own one RNG at the top level and lend it to helpers. Calls have independent state.
The [API examples](src/lib.rs) are also in the operation modules and compiled as
doctests. For a consumer beside this checkout, use
`biggie = { path = "../clis/biggie" }`.

```rust
use biggie::{Alphabet, GenerationOptions, LengthUnit, TextOptions, generate_text};
let options = TextOptions {
    generation: GenerationOptions { lines: 2, seed: Some(42), ..Default::default() },
    alphabet: Alphabet::Custom("ACGT".chars().collect()),
    length_unit: LengthUnit::Bytes,
    ..Default::default()
};
let mut data = Vec::new();
generate_text(&mut data, &options)?;
# Ok::<(), std::io::Error>(())
```

`GenerationOptions`, `generate(writer, &GenerationOptions)` and
`gen_random_lines(writer, count)` retain their ASCII text contracts. They are
compatibility entry points, not a dispatcher for other commands.

CLI parsing/option errors exit 2; corpus loading, setup, operation and reported
write/flush errors exit 1; success exits 0 after owned buffers flush. Required
errors go to stderr once regardless of log level. Named-file success prints a
completion message on stdout; single-stream stdout output contains data only.
At debug/trace, command/generation/flush spans record timings and completed counts
on successful generation. There are no per-record or input-content logs. Failures
or interruption can leave partial output. No prompts or global cwd changes occur.

## Reproducibility, scope and evidence

The same seed/options/build/platform reproduce the same data. Destination, logging
and line terminators do not change generated text words. `StdRng` and sampling may
change across versions/platforms; there is no long-term byte-format promise.
Record revision, lockfile, platform, full command and checksum. Generate once and
reuse exact inputs for candidate/reference timing; retain datasets for long-term
replay. Large data belongs under `target/` or disposable paths, not tracked fixtures.
See [benchmarking](../docs/benchmarking.md). Shared benchmark-tooling research,
measurements and examples are tracked separately in [issue #9](https://github.com/di-rs/clis/issues/9).

The former follow-up shapes—Unicode/whitespace, repeated records, known literal
matches, fields, raw byte budgets and sorted pairs—are now supported within the
contracts above. General regex/template synthesis, locale sorting, versioned replay
and filesystem trees remain outside this CLI. A filesystem fixture generator is a
separate deferred project. Styling, paging and terminal layouts are inapplicable:
they would alter generated data. Native paths are retained; only literal `-`
changes output routing. Linux and macOS are runtime targets; no Windows claim.

[CLI](tests/cli.rs) and [library](tests/library.rs) tests cover these scoped contracts,
including direct validation and short/erroring writers; they do not establish
full U1–U9 certification. Local execution is macOS arm64; Linux-only `/dev/full`
checks require Linux execution. Biggie has no direct GNU/BSD counterpart, so port
requirements P1–P4 are inapplicable. See the [north star](../docs/north-star.md) for
the shared U1–U9 requirements. Platform and feature limitations are recorded above.

[Workspace README](../README.md)
