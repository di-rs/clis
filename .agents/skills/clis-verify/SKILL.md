---
name: clis-verify
description: Use when checking a Rust change, investigating test or lint failures, or preparing verification results for a commit or PR in the clis workspace.
---

# Workspace verification

Run from the workspace root. Read `AGENTS.md`, [CONTRIBUTING checks](../../../CONTRIBUTING.md#checks), and
`prek.toml`; those files govern check commands if they change. Map edited paths
to Cargo packages, including consumers of changed utility libraries. Preserve
unrelated work and inspect the current diff before attributing failures.

Choose checks from the changed behavior and the user's acceptance criteria.
A passing E2E test for the checked code is evidence for the scenario it exercises;
do not repeat that scenario manually solely to populate the PR description.
Identify material gaps explicitly instead of treating a passing suite as proof
of behavior it never exercised.

The workspace uses nightly from `rust-toolchain.toml`, rustfmt, Clippy, and
cargo-nextest. Check missing prerequisites and report unavailable verification;
do not silently substitute a smaller suite or install tools as incidental work.

Before editing Rust, establish the affected-package baseline. After editing, follow
the package-then-workspace commands in CONTRIBUTING against the final diff; that
guide is the single source for the command list. Select actual packages and include
consumers of shared changes. Package doctests require a library target. Build the
affected binary or consumer example when relevant to the change. Reuse available
baseline results when their inputs remain unchanged.
Inspect `git diff --check` and the final status too. Formatting checks must not
rewrite unrelated Rust files. A passing package command supports a package claim;
a green workspace claim requires the workspace commands to pass.

For documentation or skill-only changes, validate changed skills, Markdown
links, and the template as applicable. Rust builds, tests, formatting, and Clippy
are unnecessary when Rust sources, manifests, and check configuration stay
unchanged, unless explicitly requested.

Reuse passing results while the checked code and configuration remain unchanged,
including across review, commit, and PR preparation. Rerun only checks affected by
new changes, failures, or unresolved concerns. Changed fixtures, dependencies,
toolchains, or relevant environment settings can invalidate previous results too.
A PR's Testing/Validation section reports these results; it is not an additional
verification step.

Keep command output as working evidence; summarize outcomes concisely in the PR
without logs, test counts, or a history of repeated runs. If a failure existed before
the change, report the baseline evidence separately. If no baseline was captured,
reproduce on an unchanged base in an isolated checkout before labeling it
pre-existing. Do not weaken tests, suppress lints, skip hooks, or fix unrelated
Kara code to obtain a green result. Kara changes also require checking affected
interactive behavior in a terminal; automated checks alone do not cover it.

Deliver a concise result: affected checks, workspace checks, baseline failures,
and anything unverified. A skipped or failed required check remains a limitation.
For a requested standards assessment, use [clis-audit](../clis-audit/SKILL.md) to map
evidence to requirements; successful execution alone is not a compliance verdict.
