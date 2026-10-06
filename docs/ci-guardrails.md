# Rust guardrail assessment and proposed rollout

[Project overview](../README.md) · [Contributor checks](../CONTRIBUTING.md#checks) ·
[North star](north-star.md)

## Implementation planning update

On 2026-10-06 the user selected the full applicable guardrail rollout and
explicitly excluded release work. Publishing, package/release validation,
release artifacts, SBOM and provenance are outside the implementation plan.
Optimized-profile correctness tests and benchmarks remain in scope; they do
not publish a release. Release recommendations below are retained as research.

The implementation base was refreshed to fetched `origin/master`
`b3b80d24557203acf1e9128fcda7a58268787e69`. It adds checked-operation cleanups and
Biggie's seeded generation, record shapes, line endings and stdout streaming.
Use these existing controls for ASCII benchmark inputs; do not reimplement them.
Their reproducibility promise is scoped to the same build/platform, so retain
generator revision, lockfile, full options and input checksums.

Fresh native macOS checks at this revision passed: workspace nextest (737 tests,
zero skipped), workspace doctests (three), and all-target/all-feature Clippy with
`-D warnings -F unsafe_code`. Cargo metadata still lists 21 members and no member
features. The workflow/manifests and Kara's experimental `!` use did not change
between the two assessed revisions. GitHub settings were re-read: the ruleset
is still disabled and Dependabot security updates remain disabled. No remote
settings were changed. The original assessment and its older command results
below remain identified by their original revision rather than being relabeled
as fresh results.

The implementation sequence has nine workstreams: compiler/local hooks;
Linux/macOS and isolated-package CI; actual merge enforcement; dependency
policy; documentation/metadata checks; library and macro regression checks;
CodeQL/coverage/toolchain probes; fuzzing and versioned GNU tail comparisons;
and correctness-checked benchmarks. The complete non-release rollout includes
all nine, with dependency order, exact files/commands and failure acceptance
checks in the local implementation plan. Repository policy intentionally ignores
that planning directory; this assessment remains the shareable repository record.
Beta and targeted Miri probes are included as scheduled evidence. Fuzz/reference
and domain enforcement begin with named, tested package slices; extending those
to every utility is a later migration, not implied compatibility certification.

## Original assessment

Assessed on 2026-10-06 against fetched `origin/master`, commit
`2d824dd7f46b67e68ca2cf628c2f565d4b640531`. These are recommendations, not enabled
checks or a certification of U1–U9/P1–P4. This assessment covers repository
guardrails; individual utility compatibility, terminal behavior, and performance
remain outside its verification scope.

The supplied RIST checklist is useful as a starting point, but this repository
contains 21 packages for command-line utilities, libraries, and a terminal editor.
It has no RIST, GStreamer, network-protocol simulator, or cryptography architecture
to protect. The existing engineering guides already define substantial standards.
The immediate opportunity is to enforce them consistently and gather evidence.

## Current checks and actual gaps

| Area | Current evidence | Recommended action |
| --- | --- | --- |
| Merge enforcement | GitHub reports `master` unprotected. Ruleset `24529961`, named `main`, is disabled, has no included refs, and only deletion/force-push rules. | Activate a correctly targeted ruleset after required jobs have produced successful runs. Failed CI does not currently imply a blocked merge. |
| Workflow | `.github/workflows/pull-request-check.yml` runs on PRs to `master`, on Ubuntu 24.04. | Add pushes to `master`, manual dispatch, native macOS tests, and scheduled maintenance. Add `merge_group` only if a merge queue is adopted. |
| Formatting and linting | CI checks formatting, runs every prek hook, and rejects tracked-file changes. Workspace Clippy includes pedantic/nursery and explicit safety-related restrictions; all members inherit it. | Keep these checks; add `-- -D warnings` to Clippy and explicit compiler warning policy on the pinned toolchain. |
| Reproducibility | Root lockfile is committed; fetch and doctests use `--locked`. Contributor commands use it, but Clippy/nextest hooks do not. Toolchain is floating `nightly`. | Lock every dependency-resolving build/test/doc invocation and pin a tested nightly date. Do not add unsupported `--locked` flags to fmt or audit. |
| Tests | Nextest and separate doctests already run. Original assessment baseline: 718 tests and two doctests pass. | Keep nextest; configure bounded test timeouts, useful failure reports, and no automatic retries hiding flakes. |
| Docs build | Contributor guide requires affected-library docs for API changes; CI runs doctests but does not build workspace rustdoc. | Add `RUSTDOCFLAGS='-D warnings' cargo doc --locked --workspace --all-features --no-deps`. It passes locally. |
| Platforms | Linux CI; Linux and macOS are explicit runtime targets in U7. Native macOS baseline passes locally. | Make both OS test jobs required. Windows is a separate portability project, not a current contract. |
| Toolchain support | No package declares `rust-version`. Kara still uses `type Err = !` in `kara/src/editor/line.rs:180`. | Retain nightly initially. Stable/MSRV support requires a scoped migration and measured compiler/dependency floor. |
| Features and isolation | Cargo metadata shows no package features or optional dependencies exposed as features. Dependencies do use selected features. All 21 packages pass independent `cargo check --locked -p PACKAGE --all-targets`. | Add per-package builds, then feature checks automatically when package features appear. An empty powerset adds little today. |
| Unsafe code | Earlier `utils/error` helper was removed on latest master. Current strict Clippy with `-F unsafe_code` passes. | Add inherited `unsafe_code = "forbid"` and verify every member inherits lints. Any future exception needs an explicit policy change and evidence. This does not certify dependencies as unsafe-free. |
| Supply chain | RustSec audit, pinned action SHAs, pinned tools/checksums, read-only permissions, disabled checkout credentials, actionlint, zizmor, and gitleaks already exist. | Preserve them. Add cargo-deny license/source policy and a scheduled refreshed audit. |
| Current audit | Audit succeeds for 217 dependencies with a warning for yanked `chacha20 0.10.1`, reached through `rand` in biggie and cutr dev-dependencies. | Review an available non-yanked replacement in a separate dependency update. Do not describe a yank as a confirmed vulnerability. |
| License metadata | Root MIT license exists, but 14 member manifests do not declare/inherit a license. | Normalize member metadata before making license policy blocking; choose the dependency allowlist from actual SPDX expressions. |
| GitHub security | Secret scanning and push protection enabled; Dependabot security updates disabled; CodeQL default setup not configured. Weekly version-update configuration exists. | Enable security updates separately from version updates; add Rust/Actions CodeQL initially as a reported scan. |
| Guidance | North star, architecture, compatibility, observability, benchmarking, utility template, PR template, and five repository skills exist. | Extend their enforcement and link this assessment; do not duplicate the standards in another framework. |

GitHub settings are a dated read-only snapshot, not changes made by this assessment.
API evidence: `GET /repos/di-rs/clis/branches/master`, `/rulesets`,
`/rulesets/24529961`, repository `security_and_analysis`, and
`/code-scanning/default-setup`.

## Recommended rollout

### First: dependable PR gates

1. **Pin nightly and align commands.** The installed nightly distribution is dated
   `2026-10-04` (`rustc 1.101.0-nightly`, commit `db8f076d2`). Use this as the
   candidate pin, verify its components on both runners, and update it through
   reviewed changes. Keep a scheduled moving-nightly probe to expose upcoming
   breakage. Pinning a nightly is not an MSRV declaration.
2. **Test Linux and macOS.** Run workspace nextest and doctests on both; run
   formatting, strict Clippy, rustdoc, and tooling/security checks on Linux.
   Record native runner OS and compiler identity. Use explicit runner labels.
3. **Make hook coverage match changes.** Current Rust-only hook filters omit
   manifest-only and fixture-only local commits. Include Cargo manifests/lockfile,
   toolchain/lint configuration, and test fixtures for the relevant hooks. CI's
   `--all-files` already runs the hooks; do not mislabel this as a CI bypass.
4. **Prevent new unsafe code and unhandled warnings.** Inherit the Rust unsafe
   lint, retain existing Clippy policy, and deny warnings on the pinned required
   jobs. Do not enable every optional lint, globally demand `missing_docs`, or
   remove narrowly justified allowances simply to maximize the lint count.
5. **Add independent package checks.** `cargo hack check --workspace --locked
   --all-targets` checks members separately. Also test ordinary library/binary
   builds without test targets and compile consumer examples. This covers
   feature unification and reusable-library claims better than duplicate full
   workspace runs. [cargo-hack behavior](https://github.com/taiki-e/cargo-hack#--all---workspace).
6. **Add dependency policy and hygiene.** Keep the existing auditor initially;
   use cargo-deny for licenses, sources, and explicit bans. Reject unexpected
   registries and Git sources; keep duplicate versions informational unless a
   particular duplication has demonstrated harm. Add cargo-machete after
   reviewing its initial findings and explaining any narrow exceptions.
7. **Check documentation mechanically.** Check local links and anchors on PRs,
   spell-check prose while excluding intentional test corpora, and compile real
   Rust consumer examples. Check remote links on a schedule with bounded retries;
   external site availability should not randomly block unrelated PRs.
8. **Enforce the jobs at merge.** Require PRs, successful named checks, resolved
   conversations, and protection from force-push/deletion on `master`. Bind checks
   to the GitHub Actions app and require an up-to-date base. Do not impose an
   impossible second-person approval on a solo maintainer. Add CODEOWNERS only
   once eligible reviewers are identified. [GitHub rules](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets).

Recommended stable check names are `quality`, `tests (linux)`, `tests (macos)`,
`packages`, `dependencies`, `workflows`, `secrets`, and `docs-policy`. Configure
required checks only after those exact names have run; changing YAML alone does
not activate branch protection. Avoid workflow-level path filters on required
checks. If a future optional job is skipped, do not let a generic aggregate job
turn required failures or skips into success.

### Next: enforce the existing library and behavior contracts

| Contract | Useful guardrail | Scope and limitation |
| --- | --- | --- |
| U1/U8: library boundaries | Compiler/Clippy checks for global streams, argv/environment mutation, exit, and process initialization in migrated library roots; direct API integration tests and external consumers. | Start with audited domains. `cli-tracing` is deliberately an adapter. `findr` currently couples its library to Clap, `lsr` prints diagnostics, and `pwdr` reads global cwd; do not break these by pretending migration is complete. |
| U2/U4: robust I/O | Inject read/write/flush failures; test partial writes, short reads, malformed input, raw bytes, Unicode boundaries, huge counts, and repeated calls. | Use package-local tests and actual documented semantics. Existing coverage is a starting point, not a reason to repeat identical tests everywhere. |
| U5/U6: process behavior | Bounded tests for broken pipes, cancellation, TTY versus redirected output, non-UTF-8 paths, and partial failure. | Reference-specific status/diagnostic expectations; Kara gets a separate terminal test plan. |
| U3: usable APIs | Rustdoc, compiled examples, and meaningful doctests for exported operations. | Current two doctests are useful but not broad public API evidence. README prose is not automatically compiled. |
| U9: logging lifecycle | Keep cli-tracing/biggie tests for level precedence, required diagnostics at level off, unchanged stdout, worker logs, repeated setup, and sink/flush failures. | These checks already exist. Add them for each newly migrated consumer, without making every utility adopt the runner in one CI change. |
| P1/P3: compatibility | Versioned case manifests and scheduled reference comparisons, preserving stdout bytes, stderr, status, and filesystem effects. | Use explicitly identified GNU/util-linux references. Ordinary tests remain hermetic; missing references fail/report unavailable instead of silently using host BSD commands. |
| U8/P4: performance | Release-profile correctness tests, coverage reports, and correctness-checked benchmark recipes. | Release tests are not timings. Shared-runner noise cannot substantiate a hard performance claim. |

A source grep can help an audit, but is not a compile-time architecture proof.
Cargo dependencies apply at package level: a combined binary/library package
depending on Clap does not prove its library uses Clap. Avoid a blanket package
dependency ban that contradicts the current layout. Enforce applicable calls with
compiler lints and verify consumer behavior; introduce separate crates or an
optional CLI feature only through a justified utility migration.

### Scheduled and release checks

| Check | Proposed cadence | Promotion condition |
| --- | --- | --- |
| Refreshed RustSec audit | Daily and each PR | Already blocking for vulnerabilities; resolve current yank before making yanks blocking. Failed database refresh must fail the job. |
| Release-profile nextest | Weekly and before a release | Add to PRs if runtime remains small; catches optimization/overflow configuration differences. |
| Moving nightly and stable probes | Weekly | Advisory toolchain probes; keep required pinned-nightly results independent. Stable is not promised while Kara fails. |
| Rust and Actions CodeQL | PRs, master, and weekly | Report initially; make scan execution/appropriate alert severity required once extraction and findings are reviewed. |
| Coverage with cargo-llvm-cov | Weekly, downloadable HTML/LCOV | Report per library; no arbitrary percentage floor. Validate child-process coverage before trusting CLI totals. |
| Fuzzing | Manual/weekly initially; short PR smoke after stable operation | Start with `parsu::parse_xml` and tailr's count/stream APIs. Bound input/time/memory, retain failures and corpus, convert found defects to regressions. |
| GNU differential checks | Manual/weekly as each utility's cases acquire provenance | Full interop is not required for every PR. Run checked-in regression cases on every PR. |
| Remote links | Weekly | Report real stale links; do not ignore all HTTP failures to manufacture success. |
| Benchmarks | Manual/dedicated runner after validated recipes | Follow P4. Preserve raw measurements, reference/base revisions, correctness and variance. No timing work is part of this research change. |
| Release packaging | Before an actual release | Explicit package allowlist, metadata/LICENSE/README validation, package contents, `cargo publish --dry-run --locked`, release binary smoke, checksums, provenance and SBOM if distributing artifacts. Never auto-publish from a PR. |

Rust is now supported by CodeQL, including editions 2021 and 2024; its documented
Rust setup uses build mode `none`. Do not dismiss it based on older advice that
CodeQL lacks Rust support. [Languages](https://codeql.github.com/docs/codeql-overview/supported-languages-and-frameworks/),
[build modes](https://docs.github.com/en/code-security/reference/code-scanning/codeql/build-options-for-compiled-languages).

Use narrowly scoped `security-events: write` only in the scan job that uploads
results, preserving read-only build/test jobs. Follow fork-PR permission behavior;
do not use a privileged `pull_request_target` job to execute PR code. Preserve
existing action SHA pins and tool verification. [GitHub security guidance](https://docs.github.com/en/actions/reference/security/secure-use).

## Corrections to the supplied checklist

- **Audit versus deny:** cargo-deny fetches/checks RustSec by default when its
  advisory check runs; an explicit advisory section is not required. Both tools
  are not mandatory duplicate gates. Keeping existing audit plus deny's
  license/source/bans checks gives clear responsibilities. If consolidating later,
  verify refreshed advisory checking and failure on unavailable data.
  [cargo-deny advisory configuration](https://embarkstudios.github.io/cargo-deny/checks/advisories/cfg.html).
- **Feature combinations:** default/full/powerset runs become valuable when member
  features exist. Introduce no-default, each-feature and small powerset checks
  with the first optional CLI feature. `--all-features` does not turn on every
  feature in every dependency. [Cargo features](https://doc.rust-lang.org/cargo/reference/features.html).
- **MSRV:** `rust-toolchain.toml` selects a development compiler; `rust-version`
  declares support. Never copy RIST's 1.88 floor into this repository without
  validating syntax, APIs, test dependencies and lockfile. [Cargo Rust version](https://doc.rust-lang.org/cargo/reference/rust-version.html).
- **Unsafe:** forbid first-party unsafe now that it is absent. It is not a claim
  that all dependencies are safe, nor proof that safe code cannot panic or hang.
  [Rust lint levels](https://doc.rust-lang.org/rustc/lints/levels.html).
- **Windows:** U7 requires Linux and macOS. `lsr` uses Unix APIs and several tests
  import Unix permission extensions. A Windows job needs an explicit supported
  subset or portability work; excluding failures cannot establish Windows support.
- **Minimal versions:** Cargo explicitly discourages blanket transitive
  `-Z minimal-versions`. If published libraries need lower-bound testing, evaluate
  direct-minimal versions in a disposable lockfile workflow; it intentionally
  resolves a different dependency graph. [Cargo warning](https://doc.rust-lang.org/cargo/reference/unstable.html#minimal-versions).
- **Miri, semver, vet:** useful when there is an applicable test subset, public
  library release baseline, or a maintained audit process. They are not automatic
  merge blockers simply because the project uses Rust. Fuzzing/compile-fail tests
  currently offer clearer targets in the parsers and comprehension macro.
- **Cryptography bans and network interop:** no evidence supports importing
  RIST's OpenSSL/ring/aws-lc bans or its interop matrix into these CLIs.

## Verification performed on the assessed revision

Native host: `aarch64-apple-darwin`; nightly distribution `2026-10-04`, rustc
`1.101.0-nightly (db8f076d2 2026-10-03)`; nextest `0.9.146`.
No production/configuration files were edited during the baseline.

| Command | Result |
| --- | --- |
| `cargo nextest run --locked -p biggie -p cli-tracing -p lsr -p comp_python_macro --hide-progress-bar --failure-output final` | Exit 0; 56 tests passed. |
| `cargo fmt --all -- --check` | Exit 0. |
| `cargo clippy --locked --workspace --all-targets --all-features` | Exit 0. |
| `cargo nextest run --locked --workspace --hide-progress-bar --failure-output final` | Exit 0; 718 passed, zero skipped. |
| `cargo test --locked --workspace --doc` | Exit 0; two doctests passed. |
| `RUSTDOCFLAGS='-D warnings' cargo doc --locked --workspace --all-features --no-deps` | Exit 0. |
| `cargo clippy --locked --workspace --all-targets --all-features -- -D warnings -F unsafe_code` | Exit 0; supports the proposed first-party lint policy. |
| `cargo check --locked -p PACKAGE --all-targets`, separately for all 21 metadata-listed members | Every invocation exited 0. This includes examples/bench compilation, not benchmark execution. |
| `cargo audit --file Cargo.lock` with database refresh | Exit 0; 217 dependencies, one allowed yanked-crate warning. |
| `cargo +stable check --locked -p kara` using installed stable 1.99.0 | Exit 101; E0658 for the experimental `!` type at `kara/src/editor/line.rs:180`. |

Linux workflow execution, new-tool baselines (deny, machete, coverage, fuzz,
CodeQL), release-profile tests, MSRV verification, packaging and benchmark timings
were not run for this assessment. Local nextest does not certify GNU parity or
interactive Kara behavior. The earlier checkout's 700-test baseline and removed
unsafe helper are not evidence for current master.

Useful implementation references: [cargo-machete](https://github.com/bnjbvr/cargo-machete),
[nextest timeouts](https://nexte.st/docs/features/slow-tests/),
[coverage](https://nexte.st/docs/integrations/test-coverage/),
[fuzz CI](https://rust-fuzz.github.io/book/cargo-fuzz/ci.html),
[trybuild](https://github.com/dtolnay/trybuild),
[license policy](https://embarkstudios.github.io/cargo-deny/checks/licenses/cfg.html),
[source policy](https://embarkstudios.github.io/cargo-deny/checks/sources/cfg.html),
[publish dry runs](https://doc.rust-lang.org/cargo/commands/cargo-publish.html).
