# cli-bench local acceptance evidence — 2026-10-07

[Harness](../../tools/cli-bench/README.md) · [Benchmark policy](../benchmarking.md) ·
[Implementation plan](../superpowers/plans/2026-10-06-cli-bench.md)

Local implementation and verification are recorded below. Native Linux and hosted
GitHub Actions execution remain unobserved. These results establish harness
behavior on macOS; they do not establish an optimization, GNU parity for the
utilities, or a comparison across hosts. The final CI scope is affected-only
benchmarks, unconditional tests and one bounded same-repository PR comment.
Remote artifacts and remote history are deferred to [#12](https://github.com/di-rs/clis/issues/12).

## Retained local evidence

Paths in this section are relative to the workspace root and intentionally ignored
by Git. They are available in this checkout, not distributed with this document.

| Location | Contents |
| --- | --- |
| `.cli-bench/` | Genuine marked store copied from the native acceptance store; runs, artifacts and datasets remain outside `target/`. |
| `.cli-bench/acceptance/task11/acceptance-index.json` | Exact native argv, run IDs, outcomes, sample counts and original bundle paths. |
| `.cli-bench/acceptance/task11/bundles/` | Portable exports, including successful input-only and failed full-resource replay evidence. |
| `.cli-bench/acceptance/task11/preservation.json` | Prior copy verification: 73,699 files / 1,742,131,318 bytes, every SHA-256 matched. Mapping is explained in `PRESERVATION.md`. |
| `.cli-bench/acceptance/sdd/2026-10-06-cli-bench/` | Preserved task reports, review reports and logs, including final Task 12 and Task 13 replacement verification. |
| `.cli-bench/acceptance/sdd/preservation.json` | Source/destination paths, sizes and SHA-256 verification for the handoff copy. |

Original evidence remains intact. Recorded temporary paths and provenance were
not rewritten; use the preservation mappings to find their durable copies. The
superseded Task 13 reports are historical evidence, not the current CI contract.
No native measurements were repeated for this documentation change.

## Final Rust and CI checks

Final Rust sources/manifests/lockfile/toolchain are unchanged since Task 12's
accepted fix. Task 13 replacement rechecked this identity and its README-included
crate documentation. Task 14 reuses those recorded results and adds the exact
examples check. All commands below exited zero; historical failures remain in the
logs rather than being replaced by successful results.

| Command from workspace root | Recorded result / retained log |
| --- | --- |
| `cargo nextest run --locked -p cli-bench` | 237 passed, 7 skipped; `task-12-logs/nextest-default-fixed.log`. |
| `cargo test --locked -p cli-bench --doc` | 5 passed: 2 ordinary + 3 compile-fail; `task-13-replacement-logs/package-doctest-final.log`. |
| `cargo clippy --locked -p cli-bench --all-targets --all-features` | Passed; `task-13-replacement-logs/package-clippy-final.log`. |
| `cargo doc --locked -p cli-bench --no-deps` | Passed; `task-13-replacement-logs/package-doc-final.log`. |
| `cargo check --locked -p cli-bench --examples` | Passed in Task 14; `task-14-logs/examples-check.log`. |
| `cargo fmt --all -- --check` | Passed; `task-12-logs/workspace-fmt.log`. |
| `cargo clippy --locked --workspace --all-targets --all-features` | Passed; `task-12-logs/workspace-clippy.log`. |
| `cargo nextest run --locked --workspace` | 1009 passed, 7 skipped; `task-12-logs/workspace-nextest.log`. |
| `cargo test --locked --workspace --doc` | 14 passed; `task-12-logs/workspace-doctest.log`. |
| `CLIS_BENCH_WORKSPACE_MANIFEST="$PWD/Cargo.toml" cargo nextest run --locked -p cli-bench --run-ignored only -E 'test(repository_acceptance_)'` | 3 passed; `task-12-logs/recipe-acceptance.log`. |
| `UV_CACHE_DIR=/tmp/task13-uv-cache uv run --frozen --project tools/cli-bench/ci python -m unittest discover -s tools/cli-bench/ci` | 29 passed: selection 8, impact 12, projection 6, native adapter 3; `task-13-replacement-logs/python-final.log`. |
| `node --test tools/cli-bench/ci/test_comment.js` | 21 passed against the actual inline workflow script with an offline API; `task-13-replacement-logs/comment-final.log`. |
| `actionlint` | Passed; `task-13-replacement-logs/actionlint-final.log`. |
| `shellcheck tools/cli-bench/ci/native.sh tools/cli-bench/ci/setup-native.sh` | Passed; `task-13-replacement-logs/shellcheck-final.log`. |
| `bash -n tools/cli-bench/ci/native.sh tools/cli-bench/ci/setup-native.sh` | Passed; `task-13-replacement-logs/shell-syntax-final.log`. |
| `zizmor --offline --no-progress .github/workflows` | No findings/warnings; `task-13-replacement-logs/zizmor-final.log`. |
| `zizmor --offline --no-progress --persona auditor --no-ignores .github/workflows` | No findings/warnings or suppressions; `task-13-replacement-logs/zizmor-auditor-final.log`. |

Log paths in the table are relative to the preserved SDD directory above. The seven
ordinary skips are four native plumbing opt-ins and three repository-resource
opt-ins; the latter passed separately. Earlier native plumbing fixtures and real
workloads have their own evidence, not ordinary-suite native coverage. Task 12's
initial dataset-fault test timeout and approved test-only deadline correction are
retained in `task-12-report.md` and its original/final logs. No unrelated Kara fix
or weakened lint/test policy was used to obtain these results.

Recorded Rust compiler: `rustc 1.101.0-nightly (db8f076d2 2026-10-03)`, full commit
`db8f076d2619ce2585b0380dda06e8da25a40da4`, `aarch64-apple-darwin`, LLVM 23.1.1.
Task 13 tools: uv 0.12.23, managed Python 3.14.7, Node 26.10.0, actionlint 1.7.12,
ShellCheck 0.11.0 and zizmor 1.30.1. Tool output is retained in the corresponding
version logs; CI's configured versions are described in the harness README.

## Native macOS scope and identities

Task 11 ran on macOS aarch64 (Darwin 27.0.0 in the manifest; Cargo reported macOS
27.0.1), Apple M5 Max, 68,719,476,736 bytes of memory. Filesystem and non-mutating
umask probes were unavailable. Observed tools are Hyperfine 1.20.0 and GNU
Coreutils 9.12 `tail`/`mkdir`, identified by canonical executable paths, version
output, bytes and hashes in `task11/reference-tools.json`. For example, GNU tail's
SHA-256 is `d95ff62338b44e01a0b2ed7cd888d5a917eaf2502a176d5132e89d2d99bab26b`.
The prebuilt role record itself does not infer package provenance; this separate
version capture binds the actual tools. Biggie has no GNU reference.

The earlier full/smoke matrices and replay use retained harness SHA-256
`c5e7efdba66041df6926c29e39e20fbab7ce1e567eb756a98bbe4240a931add3`.
Git candidate was `4e7dce44283b595de536dfd75fbb95139b021d0a`; previous and generator
pin were `0d8caa8387d446e91ef263c1ecab88870b735eb5`. Utility source comparisons were
identical, so their measured differences are plumbing evidence only. Prebuilt full
runs include primary and confirmation cases; Git full primary and confirmation
selections were separate runs with the same fixed role bindings and contract.

| Successful measurement group | Runs | Elapsed observations | RSS observations |
| --- | --- | --- | --- |
| Prebuilt smoke, three suites | 3 | 144 | 36 |
| Git smoke, three suites | 3 | 196 | 49 |
| Prebuilt full, three suites | 3 | 1440 | 180 |
| Git full primary, three suites | 3 | 960 | 120 |
| Git full confirmation, three suites | 3 | 1000 | 125 |
| Input-only strict tail replay | 1 | 108 | 27 |
| Final package-discovery Git Biggie text smoke | 1 | 8 | 2 |

Full settings are 3 checked warmups and 20 elapsed samples in each of two role-order
batches, plus 5 fresh RSS processes per role/case. Smoke has 1 checked warmup,
2 samples per batch and 1 RSS process; it suppresses conclusions. Audits retained
case/role/order/ordinal counts, warmups, final input/executable hashes, mutation
reset/effects, executable size, supported throughput and separate Darwin RSS.
`audit-results.json`, `audit-replay-results.json`, `audit-final-package-results.json`
and `full-input-identities.json` retain these checks. Reports were regenerated
offline in JSON, Markdown and terminal formats.

The final package-discovery smoke is `run-1791379789764647000-39381-0`, bound to
harness `18bbd46298e8ed008f7512c9495fa494573a9184d2071c140d004cf2a898dadc`,
validator `correctness-v1`, analysis `descriptive-v1`, and measurement contract
`6c6cf3fae7becb1940b7f4f619b7e05f9f299a0af2e2e829654e36ad07cff054`.
It exercises package metadata discovery, isolated Git builds and one common text
case. It does not relabel earlier full matrices as observations of the final
executable. Task 12 later changed one test-only timeout; no final release native
measurement of that source tree was performed.

The exact final smoke argv was:

```sh
/Users/di/.codex/worktrees/193f/clis/target/release/cli-bench run \
  -p biggie -r 4e7dce44283b595de536dfd75fbb95139b021d0a \
  -b 0d8caa8387d446e91ef263c1ecab88870b735eb5 -c text -m smoke \
  -d /private/tmp/task11-native-evidence/store \
  -H /opt/homebrew/Cellar/hyperfine/1.20.0/bin/hyperfine -f json
```

Earlier exact commands in `acceptance-index.json` use the retained older CLI's
`-s biggie|tailr|mkdirr` and `-p` previous-binary spelling. They are historical argv,
not current invocation examples. Current commands and `-P` previous syntax are in
the harness README. Every native run was exported with `-I -B`; input-only replay
additionally used `-I` without `-B` and explicit exact original role/generator/engine
bindings. Exported bytes do not guarantee execution after relocation.

Full-resource tail replay `run-1791377418045315000-28569-0` failed with zero elapsed
or RSS observations: copied system `cat` was SIGKILLed in pipeline cases. Controlled
probes retained equal hashes/modes and read-only signing metadata; the exact OS
cause remains unproven. The accepted input-only route passed all nine cases/three
roles as `run-1791378182390486000-30075-0` with 108 elapsed and 27 RSS observations.
Both outcomes and complete bundles remain preserved. Early failed reference-path,
Git-link and legacy-shape probes also remain visible in the Task 11 report/index.

## Operational limits and remaining acceptance

Cross-process exclusion across distinct data directories and lock release are
covered by the retained Task 7 integration results and final package suite. Local
compact history, immutable collision checks, interruption recovery and strict
replay have retained tests. Storage is outside `target/`, but an actual
`cargo clean` survival trial was not performed. Atomic publication/recovery is
scoped to process interruption; no directory-entry/power-loss durability guarantee
is claimed.

Native Linux reference/revision measurements and actual hosted Linux/macOS jobs
remain unobserved. Real affected-only PR execution, a comment being updated rather
than duplicated, docs-only retained-result labels, stale-run suppression and failed
partial reruns still require hosted operational observation. Offline tests cover
these branches and lint validates configuration; neither is a hosted result.
GitHub's final head-check/write interval is non-atomic, and SDK response limits are
checked after buffering; see the [transport limits](../../tools/cli-bench/ci/TRANSPORT.md).

No Docker/image setup, tool installation, workflow activation, push, PR creation,
merge, settings change, remote comment or remote history write was performed for
Task 14. The separate whole-branch review is owned by the controller. Remote
artifact/history publication is outside this release, not a pending acceptance
operation. This document and its local evidence index record the current limits
without treating unperformed work as complete.
