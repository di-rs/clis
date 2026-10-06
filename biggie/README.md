# Biggie

`biggie` generates text, literal records, delimited/CSV fields, exact-size bytes
and sorted overlapping pairs. It uses Clap, rand, optional csv, anyhow and the
workspace's shared logging setup.

## Usage

Run from the workspace root:

```sh
cargo run --locked -p biggie -- text -s 42 -n 100 example.txt
cargo run --locked -p biggie -- text -A 猫 -u scalars -n 2 -w 2 -l 3 -
cargo run --locked -p biggie -- records -r miss -r Hit -r '' -p 2 -c 2 -
cargo run --locked -p biggie -- fields -n 2 -F csv -d , -v 'a,b' -v '' -v 'a"b' -
cargo run --locked -p biggie -- bytes -b 8193 -p 00ff1b0d0a boundary.bin
cargo run --locked -p biggie -- pair -l left.txt -r right.txt -a 2 -j 3 -b 1 -c 2
```

Omitting the command selects `text`. Single-stream commands default to `out.txt`;
`-` sends data to stdout and `./-` names a literal file. Named files are created
or truncated; success prints a completion message. Failures can leave partial output.

Command-specific flags follow the command. Command names are reserved at root:
use `biggie text records`, `biggie ./records` or `biggie -- records` for a file
named `records`. Use `biggie COMMAND --help` for command help.

All commands accept `-h/--help`, `-V/--version`, and `-L/--log-level`
(`off`, `error`, `warn`, `info`, `debug`, `trace`). Logging goes to stderr;
explicit flags override `CLIS_LOG_LEVEL`, then default to off. Parsing errors
exit 2, operation errors exit 1, and success exits 0.

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

Ranges accept fixed `N` or inclusive `MIN..=MAX`. ASCII words are alphanumeric.
Non-ASCII alphabets require scalar lengths; scalars are not graphemes or display
columns. Custom alphabets reject duplicates and CR/LF; separators reject CR/LF.

## Literal records

| Short | Long | Meaning and default |
| --- | --- | --- |
| `-r` | `--record STRING` | Repeatable literal records, including empty; embedded LF rejected. |
| `-f` | `--records-file PATH` | UTF-8 LF-delimited corpus; conflicts with literal records. |
| `-p` | `--repeat N` | Positive u64 copies of each adjacent record, default 1. |
| `-c` | `--cycles N` | u64 full schedule cycles, default 1; zero is valid. |
| `-e` | `--line-ending lf\|crlf` | Default LF. |
| `-N` | `--no-final-newline` | Omit only the last record terminator. |

Each record repeats `repeat` times, then the full schedule repeats `cycles`
times. Corpus files are UTF-8 and LF-delimited; CR remains literal. Content is
limited to 8 MiB and 100,000 entries. Input/output aliases are rejected.

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

Random cells are ASCII alphanumeric; plain cells exclude the delimiter.
Literal cells cycle across rows. Plain cells cannot contain delimiters or CR/LF;
CSV quotes and escapes them. No headers are emitted. Rows and literal content
are bounded to 8 MiB. CSV counts logical records, including embedded newlines.

## Bytes

| Short | Long | Meaning and default |
| --- | --- | --- |
| `-b` | `--bytes N` | Required u64 exact decimal byte budget; zero valid. |
| `-p` | `--pattern-hex HEX` | Repeating nonempty even-length hex; at most 8 MiB decoded. |
| `-s` | `--seed U64` | Seed for random bytes; conflicts with a pattern. |

Without a pattern, output contains random bytes 0–255. The budget can end
inside a pattern, UTF-8 sequence or CRLF. No terminator is added.

## Sorted pairs

| Short | Long | Meaning and default |
| --- | --- | --- |
| `-l` | `--left FILE` | Required named left destination. |
| `-r` | `--right FILE` | Required named right destination. |
| `-a` | `--left-only N` | Required u64 count of distinct left-only keys. |
| `-j` | `--shared N` | Required u64 count of distinct shared keys. |
| `-b` | `--right-only N` | Required u64 count of distinct right-only keys. |
| `-c` | `--copies N` | Positive u64 multiplicity of every key, default 1. |
| `-s` | `--seed U64` | Seed for a shared random hex prefix; omitted means no prefix. |
| `-e` | `--line-ending lf\|crlf` | Default LF. |
| `-N` | `--no-final-newline` | Omit the final terminator independently in each output. |

Default keys are zero-padded 16-digit lowercase hex ordinals. A seed adds a
shared random hex prefix. Left-only, shared and right-only blocks are byte-sorted;
`copies` repeats each key. Both destinations must be named, distinct files.

## Cargo features and Rust API

CSV is enabled by default. Disable it with
`cargo build --locked --release -p biggie --no-default-features`, or enable it
explicitly with `--no-default-features --features csv`. A build without CSV
supports plain fields and rejects `--format csv` before touching output.

Rust callers use `generate_text`, `generate_records`, `generate_fields`,
`generate_bytes` and `generate_pair` with their corresponding typed options and
caller-owned writers. These operations return `anyhow::Result<()>`, validate
before writing, retain I/O causes for downcasting and never flush caller writers.
The older ASCII entry points `generate` and `gen_random_lines` retain `io::Result`.
See the [API examples](src/lib.rs) and operation-module doctests.

The same seed/options/build/platform reproduce the same random data; bytes may
change across rand versions or platforms. Biggie has no direct GNU/BSD counterpart.

[Workspace README](../README.md) · [Contributor checks](../CONTRIBUTING.md#checks)
