# Sanctuary

![Source nonblank lines](https://raw.githubusercontent.com/di-rs/sanctuary/chore/badges/loc.svg)

Sanctuary is a platform for shell sandboxes, with Rust command-line tools, shared
libraries, and Python services. Each CLI is its own Cargo package.
Python services and repository tools share a uv workspace.

## Try a command

From the repository root with the pinned Rust toolchain installed:

```sh
cargo run -p echor -- hello world
```

For installed commands, `make install-local` installs Biggie, Parsu, Kara, Shish, cli-bench,
and cli-check into `~/.local/bin`. Add it to `PATH`; use `PREFIX=/path` to override.
The Rust tools are release binaries; the Python tools use isolated uv tool environments.
The target replaces installations with these six names; built-ins are not installed.

Choose another package from the tables below. These commands implement selected
behaviors, not complete GNU/BSD replacements; each README explains the differences.

For Python tools, install the workspace dependencies with
`uv sync --all-packages`, then run commands through `uv run`.
Frontend development has its own prerequisites in the playground guide.

## Try the browser playground

Open a pull request's playground link to try its commands without building them
locally. The [playground guide](tools/cli-playground/README.md#use-a-preview)
explains sessions, sample files, and switching builds.

## Applications

| Project | Purpose |
| --- | --- |
| [biggie](packages/biggie/README.md) | Generate text, records, fields, bytes, and sorted pairs |
| [boxie](packages/boxie/README.md) | Run isolated sandboxes through a private service API |
| [kara](packages/kara/README.md) | Edit text in a terminal |
| [parsu](packages/parsu/README.md) | Parse a restricted XML-like language |
| [shish](packages/shish/README.md) | Run shell commands |

## Commands

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

## Development tools

| Project | Purpose |
| --- | --- |
| [cli-bench](tools/cli-bench/README.md) | Run per-CLI benchmark suites with Biggie and hyperfine |
| [cli-check](tools/cli-check/README.md) | Check CLI package boundaries, tests, documentation, and benchmark coverage |
| [cli-playground](tools/cli-playground/README.md) | Try Rust CLIs in a browser terminal with disposable files and a preview URL per PR |

## Shared libraries

| Project | Purpose |
| --- | --- |
| [cli-log](utils/cli-log/README.md) | Shared structured file logging and error reporting |
| [cli-testing](utils/cli-testing/README.md) | Shared CLI test setup and byte fixture loading |

## Find your way around

The [architecture overview](docs/architecture.md) explains how commands, the playground,
and Boxie fit together. The [platform direction](docs/platform-direction.md)
describes the durable-shell north star and open design questions. The
[threat model](docs/thread-model.md) describes security boundaries.

| Directory | Contents |
| --- | --- |
| `buildins/` | Small standalone Rust CLIs |
| `packages/` | Larger applications and Boxie |
| `utils/` | Shared Rust libraries |
| `tools/` | Repository checks, benchmarks, and browser playground |
| `infra/` | Host provisioning and service composition |

## Make a change

Read the owning package's README first. Run checks for the affected package, for
example `uv run cli-check -p echor` for repository conventions.
The [CLI testing skill](.agents/skills/cli-testing/SKILL.md) explains behavioral
tests and reference fixtures; [AGENTS.md](AGENTS.md) records repository rules.
Deployment instructions live in the [shared-host guide](infra/README.md).

Licensed under [Apache-2.0](LICENSE). Preserve tutorial credits in package READMEs.
