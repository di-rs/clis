# cli-fixtures

`cli-fixtures` inspects package-owned CLI test catalogs and records output from
independent reference executables. It shares the
[schema and runtime](../../utils/cli-testing/README.md) used by the tests, so one
recipe connects the input, invocation, expected bytes, and reference origin.

## Inspect a suite

From the repository root, with the pinned Rust toolchain:

```sh
cargo run -p cli-fixtures -- --suite buildins/cutr/tests/cases.toml list
cargo run -p cli-fixtures -- --suite buildins/cutr/tests/cases.toml explain tsv_b8
cargo run -p cli-fixtures -- --suite buildins/cutr/tests/cases.toml validate
```

`list` and `explain` show candidate arguments, cwd, inputs, stdin transport,
environment overrides, expected streams/status, execution owner, and reference
differences. `validate` checks the complete catalog and reports expected files
with no catalog owner, including files used by Rust recipes. It does not infer
Rust test ownership by parsing source.

`--suite PATH` is required before the subcommand. It must identify the owning
package's `tests/cases.toml`. Commands print human-readable results to stdout,
diagnostics to stderr, and exit 0 on success, 1 on validation/execution failure,
or 2 for invalid command arguments. `--help` and `--version` need no suite. The
tool has no interactive stdin protocol; child input comes from the recipes.

## Compare a built candidate

On macOS, the pilot uses explicit `/bin/cat`, `/usr/bin/grep`, and `/usr/bin/cut`
references. BSD fixtures are platform-specific; GNU binaries are not substituted.
cutr's reference environment requires `LC_ALL=en_US.UTF-8` for multibyte character
selection. Ordinary tests need neither these references nor this tool.

```sh
cargo build -p cutr
cargo run -p cli-fixtures -- --suite buildins/cutr/tests/cases.toml compare --candidate target/debug/cutr
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
cargo run -p cli-fixtures -- --suite buildins/cutr/tests/cases.toml record --output /tmp/cutr-recording
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
cargo run -p cli-fixtures -- --suite buildins/cutr/tests/cases.toml apply /tmp/cutr-recording
cargo nextest run -p cutr --profile ci
```

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

## Verify changes to this tool

```sh
cargo nextest run -p cli-fixtures -p cli-testing -p cli-testing-macros --profile ci
cargo clippy -p cli-fixtures -p cli-testing -p cli-testing-macros --all-targets
uv run cli-check -p cli-fixtures
```

Tests use temporary catalogs and controlled reference probes for raw-byte capture,
status/stderr failures, shared destinations, staleness, and explicit promotion.
Three ignored native tests are failure probes executed and checked by another test.

[All commands](../../README.md)
