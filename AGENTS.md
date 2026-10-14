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
Shared library packages under `utils/` may have no CLI. Test, example, and
benchmark targets do not count as CLI binaries. Share reusable implementation
through libraries rather than combining commands into one package.

Keep each CLI's tests, documentation, and benchmark suites in its own package.
Suite `binary` values must match the owning package name. When splitting or
moving a CLI, update its documentation links, workspace lockfiles, and checks.

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

Run benchmarks for pull requests only, selecting affected suites or the CLIs
explicitly requested by the contributor. Do not add benchmark runs on pushes or
merges to `master`. Treat automatic one-sample runs as execution smoke checks;
report measurements and CI duration without claiming statistically significant
performance changes from those samples.
