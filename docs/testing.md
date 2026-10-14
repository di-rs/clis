# Testing

Run checks from the workspace root. Use the pinned [Rust toolchain](../rust-toolchain.toml)
and [Python version](../.python-version); install cargo-nextest for Rust tests.
The [PR workflow](../.github/workflows/pull-request-check.yml) defines the full CI
matrix, and [prek.toml](../prek.toml) defines local hooks. The
[nextest CI profile](../.config/nextest.toml) supplies timeouts and retry settings.

## Choose checks by changed area

Replace `PACKAGE` below with the affected Cargo package name.

| Changed area | Verification |
| --- | --- |
| Rust implementation | `cargo nextest run --locked -p PACKAGE --profile ci` |
| Rust library documentation | `cargo test --locked -p PACKAGE --doc` |
| Shared Rust APIs | Test affected consumers; run `cargo run --locked --manifest-path tests/consumers/Cargo.toml`. |
| CLI tests or fixtures | Follow the [CLI testing skill](../.agents/skills/cli-testing/SKILL.md). |
| Python tools | Run the [Python checks](#python-tooling). |
| Markdown | `prek run lychee --all-files --show-diff-on-failure` and `prek run typos --all-files --show-diff-on-failure` |
| Workflows | `prek run actionlint --all-files --show-diff-on-failure`; consult CI for security checks. |
| Benchmark suites | Follow [Benchmarking](benchmarking.md). |

For workspace-wide Rust changes, use the all-feature nextest and doctest commands
from CI. Nextest does not run doctests. Formatting, Clippy, rustdoc, feature and
platform checks are specified in the workflow rather than duplicated here.

## Python tooling

Use uv 0.13 or newer and the root workspace. Shared Ruff, strict mypy and pytest
settings live in [pyproject.toml](../pyproject.toml); dependencies are pinned in
[uv.lock](../uv.lock).

```sh
uv sync --locked --all-packages
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
uv run --locked pytest
```

Keep tests beside their tool under `tools/NAME/tests/`. Register new tools in the
root uv workspace and pytest test paths; run `uv lock` after dependency changes.
Use pytest functions and shared fixtures in `conftest.py`; prefer `tmp_path` and
`monkeypatch` for temporary files and environment changes.

## Playground frontend

The [CLI playground](../tools/cli-playground/README.md) uses the pinned Node.js
and pnpm versions for frontend development and builds; production serves static
assets from Python. Run from the repository root:

```sh
pnpm --dir tools/cli-playground/frontend install --frozen-lockfile
pnpm --dir tools/cli-playground/frontend check
pnpm --dir tools/cli-playground/frontend test
pnpm --dir tools/cli-playground/frontend build
pnpm --dir tools/cli-playground/frontend exec playwright install chromium
pnpm --dir tools/cli-playground/frontend test:browser
```

Gateway and infrastructure tests run with the shared Python suite. The playground
README covers Docker and gVisor smoke checks; unit tests need no Docker daemon.

## Check reporting

Run `git diff --check`. For new documentation, pass `--files PATH` to the relevant
prek hook so untracked files are checked. Report commands, outcomes and any
unverified platforms or missing tools. Reuse passing checks while the relevant
files and environment remain unchanged; documentation-only edits need no Rust run.
