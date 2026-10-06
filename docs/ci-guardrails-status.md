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
| Package isolation and features | `packages`; cargo-hack, `scripts/check_features.py` | All 21 packages pass independently. No current member features. Four detector/exit-propagation tests pass. |
| Merge enforcement | Repository ruleset `24529961` | Read-back confirms active `master` target, PR/current-base/observed Actions checks/resolved conversations and force-push/deletion protection, with empty bypass list. A draft PR alone does not prove ready-to-merge eligibility. |
| Dependency policy | `dependencies`; `bash scripts/check-dependencies.sh` | Refreshed audit: 217 dependencies, no findings. License/source negative probes fail; unavailable advisory source fails. First-party MIT metadata filled in; two unused dev-dependencies removed; yanked chacha20 updated to 0.10.2. Duplicate versions are reported, not universally banned. |
| Metadata, links, spelling | `docs-policy` | Eight Python policy tests pass; all local Markdown links pass and a broken anchor fails. Recorded benchmark SHA is an exact spelling exception. Remote links are weekly evidence. |
| Security updates | Dependabot setting/configuration | Security updates enabled and read back; existing secret scanning and push protection retained. |

The remaining library/macro, security/coverage/toolchain, fuzz/reference and
benchmark batches are in progress. Scheduled jobs require an actual recorded run
before being described as operational. The redundant historical `parsu/Cargo.lock`
is not the active workspace graph. Existing findr/lsr/pwdr library-boundary debt
and Kara's nightly requirement remain separate migrations.
