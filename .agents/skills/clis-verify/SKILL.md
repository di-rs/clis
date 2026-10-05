---
name: clis-verify
description: Use when checking a Rust change, investigating test or lint failures, or preparing verification results for a commit or PR in the clis workspace.
---

# Workspace verification

Run from the workspace root. Read `AGENTS.md`, [CONTRIBUTING checks](../../../CONTRIBUTING.md#checks), and
`prek.toml`; those files govern check commands if they change. Map edited paths
to Cargo packages, including consumers of changed utility libraries. Preserve
unrelated work and inspect the current diff before attributing failures.

The workspace uses nightly from `rust-toolchain.toml`, rustfmt, Clippy, and
cargo-nextest. Check missing prerequisites and report unavailable verification;
do not silently substitute a smaller suite or install tools as incidental work.

Before editing Rust, establish the affected-package baseline. After editing, follow
the package-then-workspace commands in CONTRIBUTING against the final diff; that
guide is the single source for the command list. Select actual packages and include
consumers of shared changes. Package doctests require a library target. Build the
affected binary or consumer example when relevant to the change.
Inspect `git diff --check` and the final status too. Formatting checks must not
rewrite unrelated Rust files. A passing package command supports a package claim;
a green workspace claim requires the workspace commands to pass.

For documentation or skill-only changes, validate changed skills, Markdown
links, and the template as applicable; run the workspace checks required by
repository guidance. No package is affected if Rust sources and manifests stay
unchanged. Existing verification of the same final Rust tree can be reused;
repeat checks when new changes or unresolved concerns warrant it.

Record exact commands, exit results, and test counts. If a failure existed before
the change, report the baseline evidence separately. If no baseline was captured,
reproduce on an unchanged base in an isolated checkout before labeling it
pre-existing. Do not weaken tests, suppress lints, skip hooks, or fix unrelated
Kara code to obtain a green result. Kara changes also require checking affected
interactive behavior in a terminal; automated checks alone do not cover it.

Deliver a concise result: affected checks, workspace checks, baseline failures,
and anything unverified. A skipped or failed required check remains a limitation.
For a requested standards assessment, use [clis-audit](../clis-audit/SKILL.md) to map
evidence to requirements; successful execution alone is not a compliance verdict.
