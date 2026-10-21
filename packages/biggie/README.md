# Biggie

Biggie generates test and benchmark inputs: random text, repeated records,
delimited or CSV fields, exact-size bytes, and sorted overlapping pairs.

Use `biggie [COMMAND] [OPTIONS] [FILE]`. Omitting COMMAND selects `text`.
Single-output commands default to `out.txt`; `-` means stdout. Named outputs
are created or truncated, and failures can leave partial files. `pair` requires
two named destinations instead.

Command-specific flags follow the command. To write to a file named like a
command, use `biggie text records`, `biggie ./records`, or `biggie -- records`.

## Common options

These options work at the root and on every command.

| Option | Meaning |
| --- | --- |
| `-L, --log-level LEVEL` | File logging threshold: `off`, `error`, `warn`, `info`, `debug`, or `trace`; overrides `SANCTUARY_LOG_LEVEL`. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |
| `help [COMMAND]` | Show command help. |

Exit codes are 0 for success, 1 for operation failures, and 2 for argument errors.
See [logging configuration](../../utils/cli-log/README.md) for file locations,
defaults, and independent stderr logging.

## Text (`text`, the default)

Positional `FILE` is optional (default `out.txt`); use `-` for stdout.

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

## Literal records (`records`)

Positional `FILE` is optional (default `out.txt`); use `-` for stdout.

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

## Fields (`fields`)

Positional `FILE` is optional (default `out.txt`); use `-` for stdout.

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

## Bytes (`bytes`)

Positional `FILE` is optional (default `out.txt`); use `-` for stdout.

| Short | Long | Meaning and default |
| --- | --- | --- |
| `-b` | `--bytes N` | Required u64 exact decimal byte budget; zero valid. |
| `-p` | `--pattern-hex HEX` | Repeating nonempty even-length hex; at most 8 MiB decoded. |
| `-s` | `--seed U64` | Seed for random bytes; conflicts with a pattern. |

Without a pattern, output contains random bytes 0–255. The budget can end
inside a pattern, UTF-8 sequence or CRLF. No terminator is added.

## Sorted pairs (`pair`)

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

## Reproducibility and library use

The same seed, options, build, and platform reproduce the same random data.
Changing dependencies or platforms may change it. See
[library and Cargo features](docs/library.md) for the Rust API and optional CSV support.

[All projects](../../README.md)
