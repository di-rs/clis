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

`darwin-time-l.stderr` is the unchanged resource trailer from native macOS 27.0
arm64 `/usr/bin/time -l /bin/sh -c 'printf "target diagnostic\n" >&2; exit 1'`
on 2026-10-07. Only the original target prefix `target diagnostic\n` was removed;
the wrapper returned 1. The initial sandbox probe could not read `kern.clockrate`
and produced no RSS field. This fixture comes from the separately authorized
outside-sandbox probe, which produced the full native trailer.

`gnu-time-v.txt` is an original representative GNU verbose-output parser fixture,
with a literal 1024 kbytes and exit zero. It was constructed using the documented
field names in [GNU time memory resources](https://www.gnu.org/software/time/manual/html_node/Memory-Resources.html)
and [invocation](https://www.gnu.org/software/time/manual/html_node/Invoking-time.html).
It is **not** a Linux native capture or evidence of a tested GNU installation.
The supported adapter uses the documented [separate resource output file](https://www.gnu.org/software/time/manual/time.html#Redirecting-Output).
Source-local original process fixtures exercise that boundary independently of
installed benchmark tools; their resource numbers make no memory-performance claim.

The ignored RSS opt-in accepts an explicitly selected native time executable:

```sh
CLI_BENCH_TIME=/usr/bin/time cargo test --locked -p cli-bench \
  --test rss native_time_adapter_contracts_plumbing_only -- --ignored --nocapture
```

It preserves and prints temporary raw roots for direct zero/one exits, target
diagnostics without a newline, normal exit 143 with the literal target diagnostic
`time: command terminated abnormally`, pipeline/file I/O and SIGTERM rejection
against expected normal exit 143. The retained native Darwin outcome is
`Signal(15)`, separately from the ordinary target's `Exit(143)`; the earlier
shell probe's numeric status 143 did not establish a native normal exit. Original
tool fixtures separately test rejection of warning text in the resource trailer
when the wrapper's numeric exit matches. Each uses a tiny original script and
literal fixture data, not actual benchmark content. The selected time binary hash, native output,
target prefix, argv, outcome and final checks are retained. Native macOS execution
passed; no Linux native execution was available. These are adapter correctness
probes, not benchmark data or performance measurements.
