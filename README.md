# Sanctuary

Sanctuary is a workspace of Rust command-line utilities, reusable libraries, and
Python services. Each project's
README describes its usage, API, and current limitations. See [Testing](docs/testing.md)
for test commands and regression guidance, and [Benchmarking](docs/benchmarking.md)
for per-CLI TOML suites using Biggie and hyperfine.

## Layout

- `buildins/`: command-line utilities, each with its own Cargo package.
- `packages/`: applications and services, including Biggie, Kara, Parsu, and Boxie.
- `utils/`: shared Rust libraries.
- `tools/`: repository checks, benchmarks, and the browser playground.
- `infra/`: host provisioning and environment composition.

Run Rust packages from the root with `cargo run --locked -p NAME -- ARGS`.
Run Python package commands with `uv run --locked --package NAME COMMAND`.
Cargo and uv resolve package names independently of their directory locations.
File inputs, test paths, and Docker build contexts still use filesystem paths.

## Packages

Applications and services in `packages/` cover data generation, sandbox execution,
text editing, and parsing. Follow each project's README for usage and limitations.

| Project | Purpose |
| --- | --- |
| [biggie](packages/biggie/README.md) | Generate text, records, fields, bytes, and sorted pairs |
| [boxie](packages/boxie/README.md) | Run isolated sandboxes through a private service API |
| [kara](packages/kara/README.md) | Edit text in a terminal |
| [parsu](packages/parsu/README.md) | Parse a restricted XML-like language |

## Buildins

Small Rust command-line utilities in `buildins/` work with text, files,
directories, and calendars. Each command has its own Cargo package and README.

| Project | Purpose |
| --- | --- |
| [calr](buildins/calr/README.md) | Print month or year calendars |
| [catr](buildins/catr/README.md) | Concatenate and number text lines |
| [commr](buildins/commr/README.md) | Compare two sorted inputs |
| [cutr](buildins/cutr/README.md) | Select bytes, characters, or fields |
| [echor](buildins/echor/README.md) | Print arguments |
| [false](buildins/false/README.md) | Return failure without output |
| [findr](buildins/findr/README.md) | Find paths by name and file type |
| [grepr](buildins/grepr/README.md) | Search text with regular expressions |
| [headr](buildins/headr/README.md) | Print the start of text inputs |
| [lsr](buildins/lsr/README.md) | List paths and file metadata |
| [mkdirr](buildins/mkdirr/README.md) | Create directories |
| [pwdr](buildins/pwdr/README.md) | Print the working directory |
| [tailr](buildins/tailr/README.md) | Print the end of files |
| [touchr](buildins/touchr/README.md) | Create files or update timestamps |
| [true](buildins/true/README.md) | Return success without output |
| [uniqr](buildins/uniqr/README.md) | Filter or count adjacent repeated lines |
| [wcr](buildins/wcr/README.md) | Count lines, words, bytes, and characters |

The Rust CLIs share structured logging to local rotating files. See
[logging configuration and the dashboard contract](utils/cli-log/README.md).

## Tools

Repository tools in `tools/` check package conventions, run benchmarks, and let
you try CLI changes in a browser.

| Project | Purpose |
| --- | --- |
| [cli-bench](docs/benchmarking.md) | Run per-CLI benchmark suites with Biggie and hyperfine |
| [cli-check](tools/cli-check/README.md) | Check CLI package boundaries, tests, documentation, and benchmark coverage |
| [cli-playground](tools/cli-playground/README.md) | Try Rust CLIs in a browser terminal with disposable files and a preview URL per PR |

## Libraries

Shared Rust libraries in `utils/` provide logging and test support for the CLIs.

| Project | Purpose |
| --- | --- |
| [cli-log](utils/cli-log/README.md) | Shared structured file logging and error reporting |
| [cli-testing](utils/cli-testing/README.md) | Shared CLI test setup and byte fixture loading |

See [LICENSE](LICENSE) for the Apache-2.0 license. Retain existing tutorial and other
attribution in individual packages when extending their implementations.
