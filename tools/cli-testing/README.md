# cli-testing

`cli-testing` connects CLI test recipes, fixture expectations, and independent
reference output. One package provides the installed command and the Rust test
library; its nested `macros/` crate implements the re-exported `#[test_case]`
attribute. Cargo requires procedural macros to use a separate crate.

Use the [Rust authoring guide](docs/testing.md) for TOML cases, injected contexts,
and manual Rust tests. This page covers inspecting and regenerating fixtures.
Ordinary Cargo and nextest tests only read committed expectations; generation
and promotion are explicit CLI operations.

## Install and check setup

From the repository root with the pinned Rust toolchain:

```sh
make install-cli-testing
export PATH="$HOME/.local/bin:$PATH"
cli-testing --help
cli-testing --json doctor
```

The target installs a release binary into `~/.local/bin`; use `PREFIX=/path` to
choose another prefix. `make install-local` includes this tool too. Without
installation, use `cargo run -p cli-testing --` before the same arguments.
The command works outside this checkout when supplied an absolute suite path.
A [companion skill](skill/SKILL.md) describes the installed command workflow.

This is an offline tool. No authentication, network endpoint, user config, or
`init` command is needed. `doctor` without a suite checks the installed tool and
reports how to select fixtures. With `--suite PATH`, it loads and validates the
catalog and reports reference file availability and platform compatibility without
executing references. A successfully inspected setup exits zero even when a
reference is unavailable on this host; normal fixture tests remain usable.

## Find and inspect cases

```sh
cli-testing --json discover --root . --limit 10
cli-testing --suite buildins/cutr/tests/cases.toml list
cli-testing --json --suite buildins/cutr/tests/cases.toml resolve tsv_b8
cli-testing --suite buildins/cutr/tests/cases.toml explain tsv_b8
cli-testing --suite buildins/cutr/tests/cases.toml validate
```

`discover` recursively finds `tests/cases.toml` under `--root` (default: current
directory), skipping symlinks, hidden directories, `target`, and `node_modules`.
Results are ordered by path. `list` returns recipes in catalog order. Both default
to 50 entries, accept `--limit 1..1000` and `--offset N`, and return `next_offset`
in JSON. Continue with that offset until it is null. Discovery validates each
returned catalog; malformed catalogs fail the command.

A case's exact name is its stable ID within its suite. `resolve NAME` returns that
ID, the canonical suite path, and the execution owner. `explain NAME` shows the
invocation, cwd, inputs, stdin transport, environment overrides, expected streams
and exit status, reference, and declared differences. `validate` also rejects
expected files with no catalog owner, including Rust-owned recipes; it does not
infer ownership by parsing Rust source.

Use `catalog` as the read-only escape hatch: it exports the complete validated
catalog as TOML, or its parsed model with `--json`. It does not run commands:

```sh
cli-testing --json --suite buildins/cutr/tests/cases.toml catalog
```

`--suite PATH` is required for every command except `doctor` and `discover`. It
must identify the owning package's `tests/cases.toml`. `--suite` and `--json` can
appear before or after the subcommand. Paths are resolved from the invoking cwd;
candidate/reference recipes run in their owning package. The tool has no stdin
protocol; child stdin comes from the catalog.

## Compare a built candidate

On macOS, the pilot uses explicit `/bin/cat`, `/usr/bin/grep`, and `/usr/bin/cut`
references. BSD fixtures are platform-specific; GNU binaries are not substituted.
cutr's reference environment requires `LC_ALL=en_US.UTF-8` for multibyte character
selection. Ordinary tests need neither these references nor this tool.

```sh
cargo build -p cutr
cli-testing --suite buildins/cutr/tests/cases.toml compare --candidate target/debug/cutr
```

Add `--case tsv_b8` to select one case. Comparison checks the candidate's committed
contract and the reference's declared exit code and stderr, then compares stdout
with any declared expected-side adaptation. Manual origins check only the candidate
contract. Rust-owned recipes are reported as requiring native tests; run them with
Cargo or nextest. The tool neither builds candidates nor searches `PATH` for them.

## Record, review, and apply

Only run trusted repository recipes: references execute with your permissions in
the package directory. They are not sandboxed. Read the package README and use the
specified upstream implementation and locale.

Choose a new output directory outside the package's `tests/` directory:

```sh
cli-testing --suite buildins/cutr/tests/cases.toml record --output /tmp/cutr-recording
```

Recording includes reference recipes owned by both TOML and Rust. Manual origins
are skipped. It writes raw `CASE.stdout` and `CASE.stderr` files, then a completed
`recording.json` containing the executable's canonical path and SHA-256, OS release,
optional version output, arguments, effective configured environment, exit status,
input/expectation hashes, and catalog hash. Relevant environment must be declared
in the catalog; unrelated inherited host variables are not copied into the manifest.

Reference exit status and stderr are checked before a capture is accepted.
Shared fixtures must receive identical bytes from every owner. Inline expectations
must already agree and are never rewritten. A failure leaves incomplete staging
for inspection and does not update expectations or create a completed manifest.
Retry into a new directory after fixing the cause.

Review `recording.json` and compare each raw stream with its declared fixture,
including binary differences. The staging files deliberately retain incomplete
UTF-8 from BSD cut. Once the changes have been reviewed, explicitly promote them:

```sh
cli-testing --suite buildins/cutr/tests/cases.toml apply /tmp/cutr-recording --dry-run
cli-testing --suite buildins/cutr/tests/cases.toml apply /tmp/cutr-recording
cargo nextest run -p cutr --profile ci
```

`apply --dry-run` performs the same validation and lists files whose bytes would
change without writing expected files. It may execute configured reference version
probes. A preview does not authorize or reserve a later apply; apply revalidates.

`apply` preflights the complete reference case set, catalog, inputs, original
expectations, executable/OS identity, artifact hashes, allowed paths, and shared
fixture conflicts. It writes only catalog-declared expected files. Missing or
edited artifacts and changes since recording require a fresh capture. A changed
expected fixture also makes the old recording stale after promotion. Files whose
bytes already match are left alone. A filesystem error during promotion reports
which files were already written; promotion is not a transaction across files.
Keep staged directories available until review and test verification finish.

For a new fixture, create its declared expected file before loading the catalog;
all referenced fixtures must exist. Populate it with an independently derived
expectation or use an empty placeholder while recording, then review the staged
reference bytes before applying. Ordinary tests will fail while a placeholder
does not match the contract.

## JSON and exit contract

Human mode prints results to stdout and failure diagnostics to stderr. `--json`
prints exactly one schema-versioned envelope to stdout, including errors, with no
human diagnostics mixed into stderr. It is a CLI envelope, not an API response:

```json
{"schema":1,"ok":true,"data":{"suite":"/repo/buildins/cutr/tests/cases.toml","id":"tsv_b8","execution":"toml"},"error":null}
```

Failure example:

```json
{"schema":1,"ok":false,"data":null,"error":{"code":"operation_failed","message":"validate failed; rerun without --json for diagnostics"}}
```

Exit codes are 0 for success (including empty pages), 1 for operation/comparison
failure, and 2 for argument parsing failures. Missing `--suite` on an operation
is an operation failure. Help/version exit 0; with `--json` their text is in
`data.text`. JSON error codes are `invalid_arguments`, `operation_failed`, and
`comparison_failed`. Errors omit raw parser input and child diagnostics, which can
contain sensitive values. Explicit catalog reads return configured fixture values;
do not store credentials in test catalogs. The tool has no authentication secrets
to read or print.

The following are representative `data` shapes; paths and counts depend on the
selected suite. Additive fields may appear within schema 1.

| Command family | Example data |
| --- | --- |
| `doctor` | `{"version":"0.1.0","mode":"offline","auth":"not_required","suite":null,"fixtures_found":false,"references":[],"next_step":"discover --root DIR, then pass --suite PATH"}` |
| `discover` | `{"root":"/repo","suites":[{"suite":"/repo/buildins/cutr/tests/cases.toml","binary":"cutr","cases":25}],"total":4,"next_offset":1}` |
| `list` | `{"suite":"/repo/buildins/cutr/tests/cases.toml","cases":[{"name":"tsv_b8","execution":"toml"}],"total":25,"next_offset":1}` (case objects include the full recipe) |
| `resolve` / `explain` | `resolve` returns the example envelope above; `explain` adds `binary`, `cwd`, `env`, `references`, and the full `case` object. |
| `validate` | `{"suite":"/repo/buildins/cutr/tests/cases.toml","cases":25,"unreferenced_expected":[]}` |
| `catalog` | `{"suite":"/repo/buildins/cutr/tests/cases.toml","catalog":{"schema":1,"binary":"cutr","cases":[]}}` (the catalog contains all recipes and references) |
| `compare` | `{"suite":"/repo/buildins/cutr/tests/cases.toml","candidate":"/repo/target/debug/cutr","cases":[{"id":"tsv_b8","status":"matched"}]}` |
| `record` | `{"suite":"/repo/buildins/cutr/tests/cases.toml","directory":"/tmp/cutr-recording","manifest":"/tmp/cutr-recording/recording.json","cases":24}` |
| `apply` | `{"suite":"/repo/buildins/cutr/tests/cases.toml","directory":"/tmp/cutr-recording","dry_run":true,"changed_files":["expected/movies1.tsv.b8.out"]}` |

Comparison statuses are `matched`, `manual_matched`, `native_required`, and
`failed`. On comparison failure, `data.cases` retains per-case statuses alongside
the error envelope; candidate stdout/stderr are not copied into JSON diagnostics.

## Verify changes

```sh
cargo nextest run -p cli-testing -p cli-testing-macros --profile ci
cargo clippy -p cli-testing -p cli-testing-macros --all-targets
uv run cli-check -p cli-testing
```

Tests exercise macro lifecycle, raw-byte capture, reference failures, stale and
conflicting recordings, dry-run promotion, JSON errors, discovery, and use outside
the repository. Three ignored native tests are failure probes executed by another
test. Shared-library changes also require testing the affected consumers.

[All commands](../../README.md)
