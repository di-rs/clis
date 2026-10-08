# clis

A Rust workspace of command-line utilities and reusable libraries. Each project's
README describes its usage, API, and current limitations. See [Testing](docs/testing.md)
for test commands and regression guidance.

## Apps

| Project | Purpose |
| --- | --- |
| [bool](coreutils/bool/README.md) | `true` and `false` exit-status commands |
| [catr](coreutils/catr/README.md) | Concatenate and number text lines |
| [commr](coreutils/commr/README.md) | Compare two sorted inputs |
| [cutr](coreutils/cutr/README.md) | Select bytes, characters, or fields |
| [echor](coreutils/echor/README.md) | Print arguments |
| [findr](coreutils/findr/README.md) | Find paths by name and file type |
| [grepr](coreutils/grepr/README.md) | Search text with regular expressions |
| [headr](coreutils/headr/README.md) | Print the start of text inputs |
| [lsr](coreutils/lsr/README.md) | List paths and file metadata |
| [mkdirr](coreutils/mkdirr/README.md) | Create directories |
| [pwdr](coreutils/pwdr/README.md) | Print the working directory |
| [tailr](coreutils/tailr/README.md) | Print the end of files |
| [touchr](coreutils/touchr/README.md) | Create files or update timestamps |
| [uniqr](coreutils/uniqr/README.md) | Filter or count adjacent repeated lines |
| [wcr](coreutils/wcr/README.md) | Count lines, words, bytes, and characters |
| [calr](calr/README.md) | Print month or year calendars |
| [biggie](biggie/README.md) | Generate text, records, fields, bytes, and sorted pairs |
| [parsu](parsu/README.md) | Parse a restricted XML-like language |
| [kara](kara/README.md) | Edit text in a terminal |

## Libraries

| Project | Purpose |
| --- | --- |
| [cli-tracing](utils/cli-tracing/README.md) | Shared CLI logging, tracing, and error reporting |
| [comp_macro](utils/comp_macro/README.md) | Python-style comprehension macro |

See [LICENSE](LICENSE) for the MIT license. Retain existing tutorial and other
attribution in individual packages when extending their implementations.
