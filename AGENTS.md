# Repository rules

## Branches and commits

Use `.agents/skills/dev-new-branch/SKILL.md` when starting development work.
Use `.agents/skills/gitmoji-commit/SKILL.md` for every commit and PR title.
Development branches must use `feat/`, `fix/`, `chore/`, `docs/`, or `refactor/`.

## One crate, one CLI

Every Rust CLI must have its own Cargo package and directory. A CLI package must
define exactly one binary, with the same name as the package and directory.
Use `src/main.rs`; do not combine independent commands under `src/bin/` or add
multiple `[[bin]]` targets. For example, `true` and `false` are separate packages
in `coreutils/true/` and `coreutils/false/`.

A CLI package may also expose a library for its implementation or consumers.
Shared library packages may have no CLI. Test, example, and
benchmark targets do not count as CLI binaries. Share reusable implementation
through libraries rather than combining commands into one package.

Keep each CLI's tests, documentation, and benchmark suites in its own package.
Suite `binary` values must match the owning package name. When splitting or
moving a CLI, update its documentation links, workspace lockfiles, and checks.

## Native workspace operations

Keep package locations in the root Cargo and uv workspace declarations. Select
Rust packages with `cargo ... -p NAME` and Python packages with
`uv run --package NAME ...`. For package-owned assets, use
`uv workspace dir --package NAME`; use Cargo metadata for Rust package locations
and local dependency relationships. Keep paths relative to their owning package
where possible. Do not add a parallel project map or task runner.

After a move, update native declarations, affected lockfiles, the independent
`tests/consumers` manifest when relevant, and documentation links. Root Python
checks discover projects recursively. See [moving projects](docs/testing.md#move-a-project).

## Verification and tooling

Use `.agents/skills/cli-testing/SKILL.md` when adding or changing CLI tests.
E2E coverage is required; unit tests are optional and must add distinct value.
Use `cli-testing` for shared test setup and package-owned `tests/inputs/` and
`tests/expected/` fixtures. Generate compatible expectations from an independent
reference with `mk-outputs.nu`; never auto-accept candidate output.

Follow [Testing](docs/testing.md) for Rust and Python checks and
[Benchmarking](docs/benchmarking.md) for TOML suites and hyperfine runs.
Use the root uv workspace and committed lockfile for Python tooling; keep shared
Ruff and strict mypy configuration at the root.

Use `--locked` in CI, release builds, hooks, and verification commands that
support it, except when the command's purpose is to update a lockfile.
Ordinary local development commands may omit it; workspace package selection
does not require it. Refresh affected lockfiles intentionally when changing
dependencies or workspace membership, and review the diff before committing.

Run checks directly with `prek run --all-files` before committing. For focused
checks, use `prek run HOOK_ID --files PATH...`. Use `prek install` to install
hooks; keep the installed hooks and Gitmoji validation enabled. Do not use
`git commit` as a test runner or repeatedly retry commits to discover failures.

Tests that invoke Git in temporary repositories must clear inherited
repository-local Git environment variables in their child processes (see
`git rev-parse --local-env-vars`) and isolate global/system Git configuration.
`git -C` alone does not isolate a repository from hook-provided `GIT_DIR`,
`GIT_WORK_TREE`, or `GIT_INDEX_FILE`. If Git reports an unexpected bare repository,
inspect configuration origins and the intended repository type before changing
`core.bare`; restore only verified accidental changes.

Run benchmarks for pull requests only, selecting affected suites or the CLIs
explicitly requested by the contributor. Do not add benchmark runs on pushes or
merges to `master`. Treat automatic one-sample runs as execution smoke checks;
report measurements and CI duration without claiming statistically significant
performance changes from those samples.

## Human-facing documentation

Use `.agents/skills/clis-documentation-pipeline/SKILL.md` when creating, editing,
or reviewing persistent documentation for human readers about CLIs, tools,
products, or architecture. It runs `write-docs`, `technical-documentation`, and
`editorial-pass` in that order. This includes READMEs, guides, references, and
architecture overviews; ordinary chat replies, agent instructions, and scratch
notes do not need the pipeline.
