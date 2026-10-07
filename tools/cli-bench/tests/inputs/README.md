# cli-bench regression fixtures

`minimal-suite.toml`, `tiny-datasets.toml`, and other workload fixtures are original
small plumbing inputs, not benchmark evidence or copied reference tests.

`hyperfine-status-0.json` and `hyperfine-status-1.json` were captured on native
macOS arm64 on 2026-10-07 using installed Hyperfine 1.20.0 at
`/opt/homebrew/bin/hyperfine`, through the opt-in public timing adapter test:

```sh
CLI_BENCH_HYPERFINE=/opt/homebrew/bin/hyperfine cargo test --locked -p cli-bench \
  --test timing native_hyperfine_adapter_contracts_plumbing_only -- --ignored --nocapture
```

Each is the first one-observation export from its expected-zero or expected-one
case. The target was an original tiny POSIX script printing `EFGH\n`, with explicit
exit 1 in the second case. Only `results[0].command` was normalized to
`fixture command` to remove ephemeral absolute paths; timing and status fields are
unchanged. JSON indentation was normalized. These fixtures test the export schema
and exact status acceptance, not elapsed-time performance.

The adapter passed `--runs 1 --warmup 0 --shell=none`, `--input null`,
`--output pipe`, a unique `--export-json` path, and the shell-words encoded command.
Only the expected-one case received `--ignore-failure=1`. Native opt-in tests also
cover a fixed Bash/cat pipeline, regular-file input/output, self-checking unusual
literal argv and actual exit zero under
expected one. The test prints and preserves temporary raw-evidence roots, including
engine version/hash, exact argv, stdout, stderr/warnings, export and final checks.
Native Linux adapter execution remains unverified.
