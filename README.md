# clis

A Rust workspace building Unix-style commands and reusable libraries. The priority
is to evolve existing utilities toward GNU parity with selected useful BSD additions,
then beat reference performance on measured workloads, while keeping code readable,
maintainable, and reusable by future Rust projects.

**Current status:** these are partial implementations, not drop-in replacements.
Library APIs exist in several packages but are not yet uniform. The [north star](docs/north-star.md)
is a development contract, not a claim of complete compatibility, stable APIs,
or demonstrated performance superiority. App READMEs describe current capabilities
and limitations.

## Apps

| App | Purpose | Existing reference |
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
| [biggie](biggie/README.md) | Generate text, records, fields, bytes and sorted pairs | No direct counterpart |
| [cli-bench](tools/cli-bench/README.md) | Correctness-checked CLI benchmark evidence | Custom harness |
| [parsu](parsu/README.md) | Parse a restricted XML-like language | No direct counterpart |
| [kara](kara/README.md) | Edit text in a terminal | No direct counterpart |

Reference manuals are linked from each app README. `grep`, `find`, and `cal` are
not part of GNU Coreutils, regardless of their location in this repository.

The table retains the existing reference mapping; it is not a compatibility
scorecard. GNU versions and BSD feature-review decisions belong in each utility's
contract. `biggie`, `parsu`, and `kara` have no direct GNU/BSD counterpart,
but the same library-reuse and code-quality goals apply to their useful operations.

## Development

Use Rust nightly, selected by [rust-toolchain.toml](rust-toolchain.toml), with
`rustfmt` and `clippy`. The toolchain currently floats rather than pinning a date;
record `rustc -Vv` when reproducing a result. Install
[cargo-nextest](https://nexte.st/docs/installation/pre-built-binaries/) for the test commands below.
Linux and macOS are the runtime targets; per-utility validation is still incremental.
Kara requires an interactive terminal.

Install [prek](https://prek.j178.dev/installation/) and enable the Git hook once
per clone with `prek install`. The configuration alone does not install the hook.

Run from the workspace root:

```sh
cargo build --locked -p catr
cargo run --locked -p catr -- --help
cargo nextest run --locked -p catr
cargo test --locked -p catr --doc
cargo build --locked --release -p catr
```

See [CONTRIBUTING](CONTRIBUTING.md) for workspace checks and fixture conventions.
The [cli-bench harness](tools/cli-bench/README.md) provides Biggie, tailr and mkdirr
suites, elapsed/RSS evidence, export and replay. Benchmarks require additional tools
described in the [benchmarking guide](docs/benchmarking.md); they are not prerequisites for ordinary tests.
CI benchmarks only affected CLIs, keeps tests unconditional, and updates one PR
comment with labelled current/retained results. It uploads no benchmark artifacts;
remote storage is deferred to [#12](https://github.com/di-rs/clis/issues/12).
The [local acceptance record](docs/evidence/2026-10-07-cli-bench-acceptance.md)
indexes observed checks, native evidence and remaining platform/hosted gaps.

## Workspace and contributor guides

Commands live under `coreutils/` and in `calr/`, `biggie/`, `parsu/`, and `kara/`.
[`utils/cli-tracing`](utils/cli-tracing/README.md) provides shared CLI logging, tracing, and error reporting;
[`utils/comp_macro`](utils/comp_macro/README.md) provides a comprehension macro.
These helpers are libraries, not commands. Process initialization belongs in CLI
adapters, not in reusable domain operations.

| Guide | Purpose |
| --- | --- |
| [North star](docs/north-star.md) | Universal standards, port requirements, and evidence statuses. |
| [Utility template](docs/templates/utility.md) | A reusable app contract with flags, limits, and audit evidence. |
| [AGENTS.md](AGENTS.md) | Mandatory repository guidance for coding agents. |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Change workflow, checks, and review requirements. |
| [Architecture](docs/architecture.md) | Library/CLI boundaries, sharing, and a Rust consumer example. |
| [Observability](docs/observability.md) | Errors, diagnostic levels and sinks, stage timing, and lifecycle. |
| [Compatibility](docs/compatibility.md) | GNU references, BSD additions, and fixture capture. |
| [Benchmarking](docs/benchmarking.md) | Fair comparisons and performance acceptance. |

Repository [agent skills](docs/agent-skills.md) cover exploration, standards audits,
regression testing, verification, and Git delivery. Every PR uses the
[shared template](.github/pull_request_template.md).

See [LICENSE](LICENSE) for the MIT license. Retain existing tutorial and other
attribution in individual packages when extending their implementations.
