# cli-check

Check whether the repository's Rust CLI packages follow its structural standards:

```sh
uv run --locked cli-check
```

Run from the repository root or a directory inside its Cargo workspace, using
the pinned Python version and uv 0.13 or newer. The root uv workspace installs
the command and its Markdown parser from the committed lockfile. A successful
run prints `CLI checks passed`.

## Choose checks

The default command runs every group. Select one group to investigate a failure:

| Command | Checks |
| --- | --- |
| `cli-check layout` | One binary per CLI, matching package/directory/binary names, and an existing `src/main.rs`; rejects CLI sources under `src/bin/`. |
| `cli-check tests` | Nonempty, enabled `tests/cli.rs`, the Rust `cli-testing` development dependency, and fixture generator conventions. |
| `cli-check docs` | Nonempty package READMEs, links from the root README, and existing local Markdown link/image targets. |
| `cli-check benches` | Package-owned benchmark suites, matching binary names, nonempty cases with unique names and argument arrays, or an explicit exemption. |

Prefix these commands with `uv run --locked`. Use `--root` to inspect another
workspace; it can appear before or after the group:

```sh
uv run --locked cli-check docs --root /path/to/workspace
```

Violations go to stderr, sorted by path and rule, with a stable rule identifier:

```text
buildins/echo/Cargo.toml: must define exactly one binary [layout:single-binary]
```

Exit status is 0 for success, 1 for violations or unreadable/invalid repository
data, and 2 for invalid arguments. Checks read files without changing them or
executing the CLIs, generators, tests or benchmarks.

## Record a benchmark exemption

A CLI without a finite benchmark must explain why in its own `Cargo.toml`:

```toml
[package.metadata.cli-check]
benchmark-exemption = "Interactive terminal editor; no finite command benchmark."
```

Kara uses this exemption. Remove the exemption when adding a suite; an exemption
and suite together fail the check. An empty `benches/` directory does not count
as coverage. See [Benchmarking](../../docs/benchmarking.md) for the full suite
format and execution commands.

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
[Rust test helper](../../utils/cli-testing/README.md) supplies command setup and
byte fixture loading; the [testing skill](../../.agents/skills/cli-testing/SKILL.md)
describes how to write behavioral tests.

The docs group parses Markdown links, images and reference links while ignoring
code blocks. It resolves local paths relative to their document, including
URL-encoded filenames. It skips external URLs and fragment-only links; links
with a filename and fragment validate the filename only. Existing link, spelling
and Rust documentation checks cover their broader scopes.
For a CLI at the workspace root, the root README is already its index entry.

## Local hooks and regression tests

The `cli-check` prek hook and Python CI job run all groups. Invoke the hook with:

```sh
prek run cli-check --all-files --show-diff-on-failure
```

Regression tests invoke the real command against temporary workspace trees:

```sh
uv run --locked pytest tools/cli-check/tests
```

The benchmark selector and playground build tool use the same typed inventory.
See [Testing](../../docs/testing.md) for the remaining repository checks.
