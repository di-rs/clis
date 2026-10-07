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
| `.cli-bench/acceptance/sdd/final-review/` | Final review, approved fixes, scoped re-review and fresh verification logs. |
| `.cli-bench/acceptance/sdd/final-review-preservation.json` | Verified final handoff: 56 files / 1,771,946 bytes, every SHA-256 matched. |
| `.cli-bench/acceptance/cleanup-trial/` | Actual isolated Cargo clean trial: complete copied real native bundle, commands/logs, before/after hashes, identical reports and retained miniature workspace; `result.json` records exact mappings and identities. |

Original evidence remains intact. Recorded temporary paths and provenance were
not rewritten; use the preservation mappings to find their durable copies. The
superseded Task 13 reports are historical evidence, not the current CI contract.
No native measurements were repeated for this documentation change.

## Final Rust and CI checks

The final review found two configuration defects: reusable callers retained push/PR
events, and missing named comparators were discovered after preparation/workloads.
The approved fix batch distinguishes caller workflow identity for package overrides
and adds shared selected-case/profile comparator preflight before preparation.
Zero-child regressions preserve submitted source and tagged failure/attempt records.
Broad review and the scoped re-review of fix commit `e8582bf` are complete. Both
findings are addressed, with no new issues or open implementation findings. Native
Linux and hosted operational observations remain unverified as described below.

The final fix batch freshly ran the package-then-workspace sequence below. All
final commands exited zero; initial regression/test-construction/lint failures and
the exact corrections remain in `final-fixes-report.md` and its logs. Current fix
logs are under `.superpowers/sdd/2026-10-06-cli-bench/final-fixes-logs/`; they and the
review reports are copied and hash-verified under
`.cli-bench/acceptance/sdd/final-review/`. Earlier logs remain in the preserved SDD
directory indexed above.

| Command from workspace root | Recorded result / retained log |
| --- | --- |
| `cargo nextest run --locked -p cli-bench` | 242 passed, 7 skipped; `final-fixes-logs/package-nextest-final.log`. |
| `cargo test --locked -p cli-bench --doc` | 5 passed: 2 ordinary + 3 compile-fail; `final-fixes-logs/package-doctest-final.log`. |
| `cargo clippy --locked -p cli-bench --all-targets --all-features` | Passed; `final-fixes-logs/package-clippy-final.log`. |
| `cargo doc --locked -p cli-bench --no-deps` | Passed; `final-fixes-logs/package-doc-final.log`. |
| `cargo check --locked -p cli-bench --examples` | Passed; `final-fixes-logs/examples-final.log`. |
| `cargo build --locked -p cli-bench` | Passed; `final-fixes-logs/build-final.log`. |
| `cargo fmt --all -- --check` | Passed; `final-fixes-logs/workspace-fmt-final.log`. |
| `cargo clippy --locked --workspace --all-targets --all-features` | Passed; `final-fixes-logs/workspace-clippy-final.log`. |
| `cargo nextest run --locked --workspace` | 1014 passed, 7 skipped; `final-fixes-logs/workspace-nextest-final.log`. |
| `cargo test --locked --workspace --doc` | 14 passed; `final-fixes-logs/workspace-doctest-final.log`. |
| `CLIS_BENCH_WORKSPACE_MANIFEST="$PWD/Cargo.toml" cargo nextest run --locked -p cli-bench --run-ignored only -E 'test(repository_acceptance_)'` | 3 passed; `final-fixes-logs/recipe-acceptance-final.log`. |
| `UV_CACHE_DIR=/tmp/task13-uv-cache uv run --frozen --offline --project tools/cli-bench/ci python -B -m unittest discover -s tools/cli-bench/ci` | 31 passed: selection 8, impact 14, projection 6, native adapter 3; `final-fixes-logs/python-final.log`. |
| `node --test tools/cli-bench/ci/test_comment.js` | 21 passed against the actual inline workflow script with an offline API; `final-fixes-logs/comment-final.log`. |
| `actionlint` | Passed; `final-fixes-logs/actionlint-final.log`. |
| `shellcheck tools/cli-bench/ci/native.sh tools/cli-bench/ci/setup-native.sh` | Passed; `final-fixes-logs/shellcheck-final.log`. |
| `bash -n tools/cli-bench/ci/native.sh tools/cli-bench/ci/setup-native.sh` | Passed; `final-fixes-logs/shell-syntax-final.log`. |
| `zizmor --offline --no-progress .github/workflows` | No findings/warnings; `final-fixes-logs/zizmor-final.log`. |
| `zizmor --offline --no-progress --persona auditor --no-ignores .github/workflows` | No findings/warnings or suppressions; `final-fixes-logs/zizmor-auditor-final.log`. |

Log paths in the table are relative to the durable final-review directory above.
The seven ordinary skips are four native plumbing opt-ins and three repository-resource
opt-ins; the latter passed separately. Earlier native plumbing fixtures and real
workloads have their own evidence, not ordinary-suite native coverage. Task 12's
initial dataset-fault test timeout and approved test-only deadline correction are
retained in `task-12-report.md` and its original/final logs. No unrelated Kara fix
or weakened lint/test policy was used to obtain these results.

Recorded Rust compiler: `rustc 1.101.0-nightly (db8f076d2 2026-10-03)`, full commit
`db8f076d2619ce2585b0380dda06e8da25a40da4`, `aarch64-apple-darwin`, LLVM 23.1.1.
Final fix tools: uv 0.12.23, managed Python 3.14.7, Node 26.10.0, actionlint 1.7.12,
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
executable. Task 12 later changed one test-only timeout. The final fix batch changes configuration
preflight in production code; no native release measurement of this final source tree
was performed. The retained matrices/smoke keep their older harness identities.

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

## Actual isolated Cargo clean trial

The trial used an owned std-only miniature Cargo workspace at
`/private/tmp/clis-cargo-clean-wtz1tjaj`, with the already installed
`nightly-2026-10-04` toolchain and offline, explicit manifest/target paths. Cargo
built and executed its 474,624-byte probe binary, then actual `cargo clean`
removed the complete owned target (Cargo reported 30 files / 1.0 MiB):

```sh
cargo +nightly-2026-10-04 build --locked --offline \
  --manifest-path /private/tmp/clis-cargo-clean-wtz1tjaj/Cargo.toml \
  --target-dir /private/tmp/clis-cargo-clean-wtz1tjaj/target
cargo +nightly-2026-10-04 clean \
  --manifest-path /private/tmp/clis-cargo-clean-wtz1tjaj/Cargo.toml \
  --target-dir /private/tmp/clis-cargo-clean-wtz1tjaj/target
```

The copied real successful native bundle was the final package-discovery Biggie
text smoke `run-1791379789764647000-39381-0`, selected through the preserved Task 11
index/mapping. All 131 files / 13,231,081 bytes matched SHA-256 across the original
preserved bundle, miniature `.cli-bench/native-bundle` before and after clean, and
its durable copy under `cleanup-trial/mini-workspace/`. Scratch and source evidence
remain intact. No workload was regenerated or measured.

The existing final debug harness rendered saved JSON, Markdown and terminal
reports before/after, all exit zero and byte-identical (115,487 / 16,965 / 15,959
bytes respectively). Its SHA-256 was
`cc85cf5412d0a2c7cc8e8d90e9e75aa81824d0f5a6b481a2cccfe3e97a544d3d`,
against checkout `2921fb79c2cf05c043e1d746bc365c69bf6a2f68`; both main debug/release
harness hashes were unchanged after the trial. Exact argv, output, full file
inventories and report hashes are retained in `.cli-bench/acceptance/cleanup-trial/`.
This proves isolated actual Cargo clean survival with real evidence; it is not a
clean of the user's main workspace or a power-loss durability test.

## Operational limits and remaining acceptance

Cross-process exclusion across distinct data directories and lock release are
covered by the retained Task 7 integration results and final package suite. Local
compact history, immutable collision checks, interruption recovery and strict
replay have retained tests. The isolated actual `cargo clean` survival trial above
passed with a copied real native bundle outside the miniature workspace's
`target/`. Atomic publication/recovery is scoped to process interruption; no directory-entry/power-loss durability guarantee
is claimed.

The retained final-review reproduction is at `.cli-bench/acceptance/final-review-probe/`
with copy/hash mapping in `.cli-bench/acceptance/final-review-probe-preservation.json`.
Its original executable/workload mutations describe the pre-fix defect. The new
CLI regression proves the same invalid named target executes zero children.

Native Linux reference/revision measurements and actual hosted Linux/macOS jobs
remain unobserved. Real affected-only PR execution, a comment being updated rather
than duplicated, docs-only retained-result labels, stale-run suppression and failed
partial reruns still require hosted operational observation. Offline tests cover
these branches and lint validates configuration; neither is a hosted result.
GitHub's final head-check/write interval is non-atomic, and SDK response limits are
checked after buffering; see the [transport limits](../../tools/cli-bench/ci/TRANSPORT.md).

No Docker/image setup, tool installation, workflow activation, push, PR creation,
merge, settings change, remote comment or remote history write was performed for
Task 14 or final review closure. Broad review found the two defects addressed here;
scoped re-review confirmed both fixes without new findings. Remote
artifact/history publication is outside this release, not a pending acceptance
operation. This document and its local evidence index record the current limits
without treating unperformed work as complete.
