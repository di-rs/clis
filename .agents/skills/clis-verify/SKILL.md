---
name: clis-verify
description: Use when checking a Rust change, investigating test or lint failures, or preparing verification results for a commit or PR in the clis workspace.
---

# Workspace verification

Run from the workspace root. Read `AGENTS.md`, `README.md` Development, and
`prek.toml`; those files govern check commands if they change. Map edited paths
to Cargo packages, including consumers of changed utility libraries. Preserve
unrelated work and inspect the current diff before attributing failures.

The workspace uses nightly from `rust-toolchain.toml`, rustfmt, Clippy, and
cargo-nextest. Check missing prerequisites and report unavailable verification;
do not silently substitute a smaller suite or install tools as incidental work.

Before editing, establish a baseline for the affected packages, then workspace
checks. After editing, repeat the relevant checks against the final diff. For a
single package such as `tailr`, use:

```sh
cargo build -p tailr
cargo nextest run -p tailr
cargo clippy -p tailr --all-targets --all-features
cargo fmt -- --check
cargo nextest run --hide-progress-bar --failure-output final
cargo clippy --workspace --all-targets --all-features
```

Select the actual package instead of copying `tailr`. For changes affecting
multiple packages, check each affected package before the workspace suite.
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
