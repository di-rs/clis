# clis

A Rust workspace of command-line utilities and reusable libraries. Each project's
README describes its usage, API, and current limitations. See [Testing](docs/testing.md)
for test commands and regression guidance, and [Benchmarking](docs/benchmarking.md)
for per-CLI TOML suites using Biggie and hyperfine.

## Apps

| Project | Purpose |
| --- | --- |
| [catr](coreutils/catr/README.md) | Concatenate and number text lines |
| [commr](coreutils/commr/README.md) | Compare two sorted inputs |
| [cutr](coreutils/cutr/README.md) | Select bytes, characters, or fields |
| [echor](coreutils/echor/README.md) | Print arguments |
| [false](coreutils/false/README.md) | Return failure without output |
| [findr](coreutils/findr/README.md) | Find paths by name and file type |
| [grepr](coreutils/grepr/README.md) | Search text with regular expressions |
| [headr](coreutils/headr/README.md) | Print the start of text inputs |
| [lsr](coreutils/lsr/README.md) | List paths and file metadata |
| [mkdirr](coreutils/mkdirr/README.md) | Create directories |
| [pwdr](coreutils/pwdr/README.md) | Print the working directory |
| [tailr](coreutils/tailr/README.md) | Print the end of files |
| [touchr](coreutils/touchr/README.md) | Create files or update timestamps |
| [true](coreutils/true/README.md) | Return success without output |
| [uniqr](coreutils/uniqr/README.md) | Filter or count adjacent repeated lines |
| [wcr](coreutils/wcr/README.md) | Count lines, words, bytes, and characters |
| [calr](calr/README.md) | Print month or year calendars |
| [biggie](biggie/README.md) | Generate text, records, fields, bytes, and sorted pairs |
| [parsu](parsu/README.md) | Parse a restricted XML-like language |
| [kara](kara/README.md) | Edit text in a terminal |

All binaries write structured events and errors to local rotating log files. See
[logging configuration and the dashboard contract](utils/cli-log/README.md).

## Libraries

| Project | Purpose |
| --- | --- |
| [cli-log](utils/cli-log/README.md) | Shared structured file logging and error reporting |
| [cli-testing](utils/cli-testing/README.md) | Shared CLI test setup and byte fixture loading |

See [LICENSE](LICENSE) for the MIT license. Retain existing tutorial and other
attribution in individual packages when extending their implementations.
