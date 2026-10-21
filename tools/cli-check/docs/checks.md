# CLI check reference

## Record a benchmark exemption

A CLI without a finite benchmark must explain why in its own `Cargo.toml`:

```toml
[package.metadata.cli-check]
benchmark-exemption = "Interactive terminal editor; no finite command benchmark."
```

Kara uses this exemption. Remove the exemption when adding a suite; an exemption
and suite together fail the check. An empty `benches/` directory does not count
as coverage. See the [cli-bench runner](../../cli-bench/README.md) for suite validation and
command-line options.

## What passing establishes

The inventory expands the root Cargo workspace's declared `members` globs and
`exclude` patterns and includes a root package. It recognizes explicit binary
targets and automatic `src/main.rs` and `src/bin/` targets, respecting `autobins`.
A CLI can also expose a library; library-only packages need no CLI-specific
tests, README registration or benchmark suite. Cargo validates manifests and the
complete target graph separately. Declare repository packages in the root
members list: implicit members introduced only through path dependencies are
outside this inventory's scope.

The tests group checks target registration, including `autotests = false`,
explicit target paths and `test = false`. It rejects `mk-outs.nu` and requires
`tests/expected/` when `mk-outputs.nu` exists. Fixture-free packages need no empty
fixture directories. Registration does not establish feature eligibility, test
completeness, assertion quality or reference correctness. The
[Rust test helper](../../../utils/cli-testing/README.md) supplies command setup and
byte fixture loading; the [testing skill](../../../.agents/skills/cli-testing/SKILL.md)
describes how to write behavioral tests.

The docs group parses Markdown links, images and reference links while ignoring
code blocks. It resolves local paths relative to their document, including
URL-encoded filenames. It skips external URLs and fragment-only links; links
with a filename and fragment validate the filename only. Existing link, spelling
and Rust documentation checks cover their broader scopes.
For a CLI at the workspace root, the root README is already its index entry.
