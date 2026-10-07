# Rust workspace guardrails

[Project overview](../README.md)

This workspace contains CLI utilities, reusable libraries and a terminal editor.
The checks below cover the supported Linux/macOS targets and the current nightly
compiler. RIST/GStreamer checks do not apply. Stable/MSRV, Windows portability,
release publication and broader utility migrations are separate work.

CI uses established tools directly. Required workflows have no path filters;
manifest and fixture changes receive the same PR checks as Rust changes.

| Check | Tool and purpose |
| --- | --- |
| `quality` | rustfmt, Clippy with warnings denied and unsafe code forbidden, and rustdoc with warnings denied. |
| `tests (linux)`, `tests (macos)` | Locked nextest and separate doctests on native runners; no retries, two-minute test timeout, ten-minute suite timeout. |
| `packages` | cargo-hack checks each member and feature combinations; Cargo runs all-feature tests and a separately locked consumer. |
| `dependencies` | Refreshed cargo-audit, cargo-deny license/source policy, and cargo-machete across the root, consumer and fuzz graphs. |
| `docs-policy` | lychee checks local links/anchors; typos checks prose while preserving intentional test inputs. |
| `workflows` | actionlint validates workflow syntax and shell commands; zizmor checks workflow security. |
| `secrets` | Gitleaks scans reachable history with redacted findings. |
| `fuzz (parsu_xml)`, `fuzz (tail_bytes)` | Cargo-fuzz replays reviewed seeds and runs a bounded smoke test, retaining corpus, logs and failures. |

[Zizmor](https://github.com/zizmorcore/zizmor) is pinned to 1.30.1 in CI and also
runs through the local `zizmor` prek hook. It scans without a token. Action SHAs
and downloaded binaries are pinned; workflow permissions are read-only except
for CodeQL's security-results upload. Checkout credentials are not persisted.

Cargo commands use `--locked` where supported. Cargo-fuzz lacks that option,
so CI fetches with `cargo fetch --locked`, builds/runs offline, and rejects
lockfile changes after each stage. This limitation is explicit in the
[fuzz instructions](../tools/fuzz/README.md).

The `domain-clippy` hook applies the separate domain configuration to biggie and
tailr libraries, which must accept caller-owned I/O and return errors rather
than initializing process-wide state. Direct library tests cover partial I/O and
raw bytes; a standalone consumer checks repeated use without CLI initialization.
Trybuild checks malformed macro diagnostics. Other utility libraries retain
their documented migration work; these checks do not certify every boundary.

Daily maintenance refreshes dependency advisories. Weekly/manual maintenance
checks remote links, coverage, optimized-profile correctness, moving
nightly/stable/beta compatibility and targeted Miri tests. CodeQL analyzes Rust
and Actions on PRs, master and weekly. These investigations expose failures
without turning them into misleading green required checks.

The master ruleset requires the ten named checks above, bound to GitHub Actions,
an up-to-date base, PRs and resolved conversations, and prevents force-push and
deletion. Settings must be verified separately from workflow files. No additional
approving reviewer is required. Dependabot handles grouped updates; RustSec scans
continue even when no PR is open.

Passing CI is evidence for these checks, not certification of all
[north-star requirements](north-star.md). Changes to check names, compiler/tool
versions and dependency exceptions need review and actual verification.
