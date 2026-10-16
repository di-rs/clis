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

Playground product tests, Boxie service tests and both infrastructure packages run
with the shared Python suite. Default tests replace the Docker boundary and need
no Docker daemon. The [Boxie infrastructure guide](../tools/boxie/infra/README.md)
covers the separate native Linux/gVisor smoke; Playground consumes Boxie's private
HTTP/WebSocket API and owns no Docker access.

## Check reporting

Run `git diff --check`. For new documentation, pass `--files PATH` to the relevant
prek hook so untracked files are checked. Report commands, outcomes and any
unverified platforms or missing tools. Reuse passing checks while the relevant
files and environment remain unchanged; documentation-only edits need no Rust run.

## Independent service packaging

Ordinary pytest excludes the `integration` marker and requires no built frontend
or Docker. After a real frontend build, explicitly run:

```sh
pnpm --dir tools/cli-playground/frontend build
uv run --locked pytest tools/boxie/tests/test_packaging.py -m integration
```

These checks build each service sdist/wheel, install it with only locked runtime
dependencies into a fresh environment, import outside the checkout, serve actual
installed UI assets, and resolve all four Compose manifests. Missing prerequisites
fail. The PR frontend job runs this gate. Image builds and native Linux/gVisor
smoke are separate checks; ordinary Docker/runc is not gVisor evidence.

Unprivileged PR quality, frontend, sandbox, binary-build and affected benchmark
checks also accept the intermediate bases `refactor/boxie-service`,
`feat/boxie-temporal` and `feat/boxie-observability`. Benchmark selection still
runs only affected or explicitly requested suites. Privileged publication and
deployment retain their trusted default-branch boundaries.

Boxie has only the PostgreSQL/Temporal lifecycle. Default pytest excludes live
services; explicit integration selection fails when dependencies are missing.
Run the isolated infrastructure tier with:

```sh
uv run --locked python tools/boxie/tests/run_integration.py
```

The [Linux lifecycle workflow](../.github/workflows/boxie-lifecycle.yml) builds
native images and `echor`, verifies interpreter imports and pinned replay/cancellation,
then runs `run_integration.py --runtime runsc --browser`. This requires Linux amd64
Docker with configured gVisor, built `boxie:lifecycle`, `cli-playground:lifecycle`
and `clis-playground-sandbox:lifecycle` images, `target/release/echor`, and Chromium.
`BOXIE_NATIVE_BINARY` may select the native executable. The fixture owns random
Compose projects, volumes, identity labels and fault proxies. It independently
kills API, worker, agent and Temporal; tests failed Docker deletion, actual
artifact COMMIT acknowledgment loss, collector overlap and journal failure across
agent restart; and replays production histories. Cleanup removes only verified
owned sandbox IDs and attempts every Compose layer even after a cleanup error.

The native browser config uses the fixture's real Playground origin and Linux
artifact, including same-page reconnection. Local Docker Desktop arm64/runc cannot
satisfy this gate. Local real SQL/Temporal tests and mocked Docker tests remain
separate evidence; neither establishes production activation readiness.
