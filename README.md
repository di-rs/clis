# clis

A Rust workspace of small Unix-style commands, a test-data generator, an XML-like
parser, and a terminal editor. These are partial implementations, not drop-in
replacements for the reference commands. App READMEs list capabilities and limits.

## Apps

| App | Purpose | Reference |
| --- | --- | --- |
| [bool](coreutils/bool/README.md) | `true` and `false` exit-status commands | GNU `true`, `false` |
| [catr](coreutils/catr/README.md) | Concatenate and number text lines | GNU `cat` |
| [commr](coreutils/commr/README.md) | Compare two sorted text inputs | GNU `comm` |
| [cutr](coreutils/cutr/README.md) | Select bytes, characters, or fields | GNU `cut` |
| [echor](coreutils/echor/README.md) | Print arguments | GNU `echo` |
| [findr](coreutils/findr/README.md) | Find paths by name and file type | GNU Findutils `find` |
| [grepr](coreutils/grepr/README.md) | Search text using regular expressions | GNU `grep` |
| [headr](coreutils/headr/README.md) | Print the start of text inputs | GNU `head` |
| [lsr](coreutils/lsr/README.md) | List paths and file metadata | GNU `ls` |
| [mkdirr](coreutils/mkdirr/README.md) | Create directories | GNU `mkdir` |
| [pwdr](coreutils/pwdr/README.md) | Print the working directory | GNU `pwd` |
| [tailr](coreutils/tailr/README.md) | Print the end of files | GNU `tail` |
| [touchr](coreutils/touchr/README.md) | Create files or update modification times | GNU `touch` |
| [uniqr](coreutils/uniqr/README.md) | Filter or count adjacent repeated lines | GNU `uniq` |
| [wcr](coreutils/wcr/README.md) | Count lines, words, bytes, and characters | GNU `wc` |
| [calr](calr/README.md) | Print month or year calendars | util-linux `cal` |
| [biggie](biggie/README.md) | Generate files of random text | No direct counterpart |
| [parsu](parsu/README.md) | Parse a restricted XML-like language | No direct counterpart |
| [kara](kara/README.md) | Edit text in a terminal | No direct counterpart |

Reference manuals are linked from each app README. `grep`, `find`, and `cal` are
not part of GNU Coreutils, regardless of their location in this repository.

## Development

Use Rust nightly, selected by `rust-toolchain.toml`, with `rustfmt` and `clippy`.
The test commands below also require [cargo-nextest](https://nexte.st/docs/installation/).
Some code and tests use Unix-specific APIs; do not assume Windows support.
Run commands from the workspace root:

Install [prek](https://prek.j178.dev/installation/) and enable the Git hook once
per clone with `prek install`. The configuration alone does not install the hook.

```sh
cargo build -p catr
cargo run -p catr -- --help
cargo nextest run -p catr
cargo nextest run
```

Regression tests cover supported behavior and error handling. See app READMEs for
capabilities and remaining limitations.
Kara requires an interactive terminal.

`utils/error` and `utils/logging` provide initialization helpers.
[`utils/comp_macro`](utils/comp_macro/README.md) provides a comprehension macro;
these are libraries, not commands.

See [AGENTS.md](AGENTS.md) for code, test, documentation, and benchmark conventions.
Repository [agent skills](docs/agent-skills.md) cover Git delivery, verification,
and CLI regression testing. Every PR uses the [shared template](.github/pull_request_template.md).
See [LICENSE](LICENSE) for the MIT license.
