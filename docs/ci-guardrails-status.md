# Guardrail implementation and evidence

[Contributor commands](../CONTRIBUTING.md#checks) · [Assessment](ci-guardrails.md)

This rollout starts at `b3b80d2` and excludes release publication/packaging, SBOM
and provenance. Passing these checks does not certify GNU parity or all north-star
requirements. Linux and macOS are runtime targets; stable/MSRV and Windows are
not support promises.

| Guardrail | Command/check | Evidence and scope |
| --- | --- | --- |
| Dated compiler, locked graph, unsafe prohibition | `quality`; rust-toolchain/Cargo/prek configuration | Native [first CI run](https://github.com/di-rs/clis/actions/runs/37440893625) passes. Unsafe test member accepted before policy, rejected afterwards; safe control passes. Dependencies are outside the first-party unsafe guarantee. |
| Test timeout and both platforms | `tests (linux)`, `tests (macos)` | First CI run passes. Local baseline 737 tests and three doctests; a 180-second hanging probe was terminated after 120 seconds. JUnit retained 14 days. |
| Package isolation and features | `packages`; cargo-hack, `scripts/check_features.py` | All 21 packages pass independently. Biggie now exposes optional CSV; its three feature configurations pass after merging `0d8caa8`. Four detector/exit-propagation tests pass. |
| Merge enforcement | Repository ruleset `24529961` | Read-back confirms active `master` target, PR/current-base/all ten observed Actions checks/resolved conversations and force-push/deletion protection, with empty bypass list. A draft PR alone does not prove ready-to-merge eligibility. |
| Dependency policy | `dependencies`; `bash scripts/check-dependencies.sh` | Refreshed audit: 217 dependencies, no findings. License/source negative probes fail; unavailable advisory source fails. First-party MIT metadata filled in; two unused dev-dependencies removed; yanked chacha20 updated to 0.10.2. Duplicate versions are reported, not universally banned. |
| Metadata, links, spelling | `docs-policy` | [Merged CI](https://github.com/di-rs/clis/actions/runs/37498819662) passes, including docs-policy. Eight Python policy tests pass; all local Markdown links pass and a broken anchor fails. Recorded benchmark SHA is an exact spelling exception. Remote links are weekly evidence. |
| Security updates | Dependabot setting/configuration | Security updates enabled and read back; existing secret scanning and push protection retained. |

| Domain/API and macro diagnostics | `quality`, `packages`, native tests | biggie/tailr library guards and an independently locked consumer pass. Four new tail I/O tests and four compile-fail fixtures pass. Malformed trailing macro clauses were wrongly accepted and are now rejected. Merged default/all-feature suites: 777 tests and nine doctests. Before the Biggie merge, optimized-profile testing passed 742 tests and three doctests. |
| Security and runtime probes | CodeQL, weekly maintenance | Both [CodeQL Rust/Actions analyses](https://github.com/di-rs/clis/actions/runs/37496873201) pass on the pre-merge guardrail commit. Native targeted Miri: 12 parsu and four tail tests pass. Coverage collected from 742 tests, including library and CLI files; no coverage floor. |
| Fuzzing | Required `fuzz (parsu_xml)` and `fuzz (tail_bytes)` | [Both Linux fuzz jobs](https://github.com/di-rs/clis/actions/runs/37498819633) pass on the merged commit, with uploaded corpus/log artifacts. Native macOS 15-second smoke: 8,246,664 tail and 1,530,376 XML executions without a crash. Separate graph audited; libFuzzer 0.4.13 has a scoped NCSA exception. Locked Cargo child commands and lockfile comparisons guard resolution. |
| Live GNU references | Weekly/manual compatibility | 97/97 scoped tail cases match GNU Coreutils 9.12 on macOS. Binary streams, statuses, failure evidence and fixture provenance are retained. Eight failure-path tests pass. Other utilities are outside this matrix. |
| Benchmarks | Weekly/manual benchmark workflow | Five runner tests pass, including wrong-output rejection and killing timed-out descendants. Native full timing passes correctness and retains 180 samples; merged Biggie passes smoke. Existing seekable last-lines code remains about 13 times slower than GNU in the exploratory run; collection does not accept that gap. Remote scheduled evidence remains pending. |

Root, consumer and fuzz graphs pass refreshed audit/license/source checks and
unused-dependency analysis (226, 58 and 61 locked packages respectively). All 23 Python harness/policy tests pass. Scheduled jobs require an actual recorded run
before being described as operational. The redundant historical `parsu/Cargo.lock`
is not the active workspace graph. Existing findr/lsr/pwdr library-boundary debt
and Kara's nightly requirement remain separate migrations.

The three newly scheduled workflows (maintenance, compatibility and benchmarks)
returned HTTP 404 on manual dispatch before default-branch registration. They
remain awaiting merge and real native runs. The PR merge is a separate decision;
merging master into this branch does not activate default-branch schedules.
