# Kara 0.2.0 verification

This release completes Hecto chapters 6–7 with Kara's existing modal bindings,
Ropey storage, Ratatui rendering, literal Vim-style search, and a small Rust lexer.
Appendix features and a complete Rust parser remain outside its scope.

## Dependency review

A scoped `cargo update --dry-run` reviewed Kara's storage, rendering, Unicode,
and persistence dependencies on 2026-10-23. It proposed only `rustix` 1.1.5.

| Dependency | Retained version |
| --- | --- |
| Ropey | 1.6.1 |
| Ratatui | 0.30.2 |
| Crossterm | 0.29.0 |
| rustix | 1.1.4 |
| unicode-segmentation | 1.13.3 |
| unicode-width | 0.2.2 |
| tempfile | 3.27.0 |
| exacl | 0.13.0 |
| xattr | 1.6.1 |

Ratatui and Kara share one Crossterm 0.29 backend. Ropey disables Unicode line-break
features so the document recognizes LF/CRLF; grapheme segmentation remains explicit.
Ratatui enables its Crossterm backend and layout cache without the default widget set.

The rustix patch was reviewed but not applied: it changes shared platform backends
and runtime code, and the workspace uses that same lockfile entry through terminal,
argument-parsing, and test dependencies outside Kara. Its update is deferred to
workspace dependency maintenance. The release lockfile changes only Kara's version;
this is not a whole-workspace dependency upgrade.

## Build-size comparison

Measured on `aarch64-apple-darwin` with Rust
`1.101.0-nightly (db8f076d2 2026-10-03)`, using the workspace profiles and no extra
stripping step:

| Build | Command | Executable bytes |
| --- | --- | ---: |
| Debug | `cargo build -p kara` | 9,815,176 |
| Release | `cargo build -p kara --release` | 2,427,040 |

The release executable is 75.3% smaller than the debug executable. This compares
file size, not runtime performance. Both commands build the same 0.2.0 sources.
`target/release/kara --version` reports `kara 0.2.0`.

## Validation

Local macOS validation passed: all 42 nextest tests (one intentionally ignored
child helper), formatting, debug/release Clippy with warnings denied, debug/release
builds, scoped Ruff and strict mypy, CLI checks, and local documentation checks.

Separate review agents inspected each of the 30 PRs. Nine findings across eight
PRs were corrected, with regressions covering cursor placement after joins,
wide-filename status rendering, complete-grapheme search, syntax composition,
number boundaries, missing keywords, and quoted forms versus lifetimes.
The corrected changes were propagated through the dependent branches before
this validation run.

The commands and coverage are documented in the [README](../README.md#install-and-verify).

The macOS permission fixture now compares native binary ACLs instead of resolved
account names from `ls`. It checks both preservation and absence of inherited
access, and reports before/after bytes on a mismatch. One earlier comparison of
`ls` output failed but did not reproduce in five focused stress iterations; its
cause was not established. The native comparison also distinguishes an absent ACL
from a missing fixture, and passed three focused stress iterations.

GitHub validation was queued when this record was prepared. Linux coverage and
full workspace validation remain CI responsibilities; local success does not
establish their results.

The 30 dependent draft PRs are reviewed and merged from the bottom of the stack.
After a squash merge, rebase and retarget descendants onto the updated predecessor,
and update owned branches with `--force-with-lease`. Publication workflows retain
their original branch restrictions.
