# cutr

`cutr` selects fields, Unicode characters, or bytes from text input.

## Arguments and options

| Argument or option | Meaning |
| --- | --- |
| `FILES...` | Input files; default `-` reads non-terminal stdin. |
| `-f, --fields LIST` | Select one-based fields, for example `1,3-5`. |
| `-c, --chars LIST` | Select one-based Unicode characters. |
| `-b, --bytes LIST` | Select one-based byte positions. |
| `-d, --delimiter CHAR` | Single-byte field separator; default tab. |
| `-h, --help` | Print help. |
| `-V, --version` | Print version. |

Choose exactly one of fields, characters, or bytes.

## Before using it

Unlike [GNU cut](https://www.gnu.org/software/coreutils/manual/html_node/cut-invocation.html),
field mode uses CSV parsing and writing, including quoting and consistent field
counts. Open-ended ranges and equal-endpoint ranges such as `2-2` are rejected;
selections retain the requested order and duplicates. Input must be UTF-8;
byte selections that split a character are decoded with replacement characters.

File-open errors go to stderr; remaining operands are processed and the final
exit status is 1.

## Test notes

Character fixtures require a multibyte-aware BSD `cut` and a supported UTF-8
locale. Byte fixtures retain raw reference bytes, including partial UTF-8
sequences; the relevant assertions explicitly apply replacement decoding to
match `cutr`. The duplicate-position fixture is a manual expectation because
upstream `cut` deduplicates positions.

The [case catalog](tests/cases.toml) pins macOS `/usr/bin/cut` with
`LC_ALL=en_US.UTF-8`. `tsv_b8` declares the `utf8-lossy` expectation adaptation in
TOML; `tsv_b1_8` uses `#[test_case]` in [tests/cli.rs](tests/cli.rs) to make the
same adaptation in Rust. Both keep the committed reference bytes unchanged.
Use [cli-fixtures](../../tools/cli-fixtures/README.md) to inspect, compare, or
stage the independent recipes. Other Rust scenarios remain native tests.

[All commands](../../README.md)
