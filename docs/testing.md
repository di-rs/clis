# Testing

Run checks from the workspace root. Use the pinned [Rust toolchain](../rust-toolchain.toml)
and [Python version](../.python-version); install cargo-nextest for Rust tests.
The [PR workflow](../.github/workflows/pull-request-check.yml) defines the full CI
matrix, and [prek.toml](../prek.toml) defines local hooks. The
[nextest CI profile](../.config/nextest.toml) supplies timeouts and retry settings.

## Draft pull requests

Draft PRs skip automatic checks, CodeQL, Git policy, benchmarks, Boxie lifecycle
checks, playground builds and preview publication. Jobs are skipped before
allocating a runner; GitHub may still display skipped workflow entries.
Marking a PR ready for review
starts the usual checks, subject to each workflow's existing path filters.

To run CI while keeping a PR in draft, add the `run-ci` label. Adding it starts
the checks immediately, and subsequent commits keep running CI while it remains
applied. Remove it to skip CI on future draft updates; remove and reapply it to
request another run for the same commit. Adding any label also reruns CI on ready
PRs and drafts with the override; drafts without it remain skipped. Existing
manual **Run workflow** controls remain available for individual workflows that
support them; select the PR branch.

Create the repository label once if it does not exist:

```sh
gh label create run-ci --description "Run CI on draft pull requests" --color 0E8A16
gh pr edit PR_NUMBER --add-label run-ci
```

Closing a PR still cleans up hosted previews, including previews created while
the draft override was enabled. Pushes to `master`, schedules and image release
or setup workflows retain their existing behavior. Trusted benchmark reporting
and preview publication use the new skip policy after their workflow definitions
reach `master`.

## CI display names

Give workflows, jobs and steps descriptive names starting with an emoji.
Describe the check or operation, and include the platform, language or toolchain
in matrix job names. Name action steps explicitly so CI shows their purpose
instead of an action reference and commit hash.

When renaming a workflow or job, update dependent workflow triggers, publisher
identity checks, benchmark timing lookups and required status checks together.

## Local hooks and full CI checks

Install missing Git hooks with `prek install`, then let each real commit run its
pre-commit and commit-msg hooks normally. Agents must never run prek checks
manually, invoke the hook runner directly, disable hooks, or create commits just
to test them. During development, use the focused tool commands below.

Ruff, workflow linting, Markdown links and spelling receive matching filenames.
The [package hook runner](../tools/cli-check/src/cli_check/hooks.py) selects Cargo
packages and Python test directories from the changed paths. Shared source
changes include dependent packages; test and fixture changes select their owner.
Python infrastructure tests do not trigger Rust checks. Formatting and CLI
standards checks select the owning Cargo packages.

Shared language configuration and lockfiles select all packages of that language.
A package's configuration selects that package and, for type checks and tests,
its consumers. Changing the hook configuration alone does not run every suite.
CI retains full validation, using direct Cargo commands and all-file selections
for the remaining hooks.

Wait for checks through the execution tool's session handle and read its output.
Do not use `ps`, `ps aux`, `ps -axo`, `pgrep`, `top`, or equivalent process-list
commands to monitor checks. Do not background checks or poll temporary log files.

## Choose checks by changed area

Replace `PACKAGE` below with the affected Cargo package name.

| Changed area | Verification |
| --- | --- |
| Rust implementation | `cargo nextest run --locked -p PACKAGE --profile ci` |
| Rust library documentation | `cargo test --locked -p PACKAGE --doc` |
| Shared Rust APIs | Run library doctests and the affected packages’ CLI tests. |
| CLI tests or fixtures | Follow the [CLI testing skill](../.agents/skills/cli-testing/SKILL.md). |
| Python tools | Run the [Python checks](#python-tooling). |
| Markdown | `lychee --offline --include-fragments=anchor-only -- PATH...` and `typos --force-exclude -- PATH...` |
| Workflows | `actionlint PATH...` and `zizmor --offline --no-progress PATH...` |
| Benchmark suites | Follow [Benchmarking](benchmarking.md). |

For workspace-wide Rust changes, use the all-feature nextest and doctest commands
from CI. Nextest does not run doctests. Formatting, Clippy, rustdoc, feature and
platform checks are specified in the workflow rather than duplicated here.

## Python tooling

Use uv 0.13 or newer and the root workspace. Shared Ruff, strict mypy and pytest
settings live in [pyproject.toml](../pyproject.toml); dependencies are pinned in
[uv.lock](../uv.lock). Replace `PACKAGE_PATH` with the affected directory, such as
`tools/cli-check`, and include consumers when shared code changes.

```sh
uv sync --locked --all-packages
uv run --locked ruff check PACKAGE_PATH
uv run --locked ruff format --check PACKAGE_PATH
uv run --locked pytest PACKAGE_PATH/tests
```

For mypy, reuse the hook's workspace import paths so scoped checks can resolve
local dependencies without adding them as check targets:

```sh
MYPYPATH="$(uv run --locked python -c '
from pathlib import Path
from cli_check.hooks import mypy_environment
print(mypy_environment(Path.cwd())["MYPYPATH"])
')" uv run --locked mypy PACKAGE_PATH
```

Keep tests beside their package under `packages/NAME/tests/`, `tools/NAME/tests/`,
or its infrastructure directory. Register new tools in the
root uv workspace and pytest test paths; run `uv lock` after dependency changes.
Use pytest functions and shared fixtures in `conftest.py`; prefer `tmp_path` and
`monkeypatch` for temporary files and environment changes.

## CLI repository standards

Run structural checks for the affected Cargo package:

```sh
uv run --locked cli-check -p PACKAGE
```

[cli-check](../tools/cli-check/README.md) verifies package boundaries, CLI test
registration, README links and benchmark coverage. Select `layout`, `tests`,
`docs` or `benches` after the command to investigate one group. Repeat `-p` for
multiple packages; CI omits it to check all packages. These checks read the
workspace; execute the owning package's tests separately to verify behavior.

Benchmark reporting and playground publication use GitHubKit from the root
tooling project's runtime dependencies, including when invoked with `--no-dev`.
They read `GH_TOKEN`, falling back to `GITHUB_TOKEN`. Their request limits and
publication checks remain local to each publisher.

## cli-playground Frontend

The [cli-playground](../tools/cli-playground/README.md) uses the pinned Node.js
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
no Docker daemon. The [Boxie infrastructure guide](../packages/boxie/infra/README.md)
covers the separate native Linux/gVisor smoke; Playground consumes Boxie's private
HTTP/WebSocket API and owns no Docker access.

The root pytest configuration registers the shared [dependency fixtures](../packages/boxie/tests/support/boxie_test_support/dependencies.py)
once. PostgreSQL and Temporal servers start only when requested and are shared
within the session; roles and databases remain isolated per test, with separate
teardown for each acquired resource. Request fixtures by name rather than importing
them into test modules. Keep reusable setup in support modules, use small Protocols
for checked fakes, and inject clocks and transports. Reserve monkeypatching for
environment handling and deliberate failures at system boundaries.

## Check reporting

Run `git diff --check`. For new documentation, pass its paths directly to lychee
and typos so untracked files are checked. Report commands, outcomes and any
unverified platforms or missing tools. Reuse passing checks while the relevant
files and environment remain unchanged; documentation-only edits need no Rust run.

## Independent service packaging

Ordinary pytest excludes the `integration` marker and requires no built frontend
or Docker. After a real frontend build, explicitly run:

```sh
pnpm --dir tools/cli-playground/frontend build
uv run --locked pytest packages/boxie/tests/test_packaging.py -m integration
```

These checks build each service sdist/wheel, install it with only locked runtime
dependencies into a fresh environment, import outside the checkout, serve actual
installed UI assets, and resolve all four Compose manifests. They also require a
running Docker daemon to verify that build contexts exclude locally generated
frontend assets. Missing prerequisites fail. The PR frontend job runs this gate.
Full service image builds and native Linux/gVisor smoke are separate checks;
ordinary Docker/runc is not gVisor evidence.

Unprivileged PR quality, frontend, sandbox, binary-build and affected benchmark
checks target `master`. Benchmark selection requires changes to the owning CLI's
Rust source or build script; PR-body requests cannot override it. Privileged
publication and deployment retain their trusted
default-branch boundaries.

Boxie has only the PostgreSQL/Temporal lifecycle. Default pytest excludes live
services; explicit integration selection fails when dependencies are missing.
Run the isolated infrastructure tier (including SQL/Temporal observability, migration and history/privacy checks) with:

```sh
uv run --locked python packages/boxie/tests/run_integration.py
```

The [Linux lifecycle workflow](../.github/workflows/boxie-lifecycle.yml) runs real
PostgreSQL/Temporal checks, pinned replay/cancellation, credential permissions and
endpoint restart checks on pull requests. The native lifecycle E2Es are
temporarily manual-only while their hang is investigated. Boxie lifecycle runs
without consumer sources. The separate [shared-system workflow](../.github/workflows/sanctuary-lifecycle.yml)
builds cli-playground and `echor` for application/browser coverage. To run both locally:

```sh
uv run --locked pytest -m integration packages/boxie/tests/integration/test_lifecycle.py \
  tools/cli-playground/tests/test_native.py -vv --durations=10
```

Each CI tier has its own timeout and reports individual tests and slow durations.
The optional native E2Es require Linux amd64 Docker with configured gVisor,
built `boxie:lifecycle`, `cli-playground:lifecycle`
and `boxie-sandbox:lifecycle` images, `target/release/echor`, and Chromium.
`BOXIE_NATIVE_BINARY` may select the native executable. The fixture owns random
Compose projects, volumes, identity labels and fault proxies. It independently
kills API, supervisor (including its embedded workers) and Temporal; tests failed
Docker deletion, actual artifact COMMIT acknowledgment loss, collector overlap and journal failure across
supervisor restart; and replays production histories. Cleanup removes only verified
owned sandbox IDs and attempts every Compose layer even after a cleanup error.

The native browser config uses the fixture's real Playground origin and Linux
artifact, including same-page reconnection. Local Docker Desktop arm64/runc cannot
satisfy this platform requirement. Local real SQL/Temporal tests and mocked Docker tests remain
separate evidence; neither establishes production activation readiness.

## Observability validation

The existing lifecycle job also validates pinned native Vector/Prometheus/Cloudflare
configuration and runs a compact Linux persistent-journal to Grafana datasource
smoke after a collector outage. To run the validators locally on Docker Desktop:

```sh
uv run --locked pytest -m integration packages/boxie/tests/test_observability_native.py -k native_configs
```

The full native file requires a Linux Docker host with persistent journald and
real PostgreSQL/Temporal fixtures. Missing prerequisites fail when explicitly
selected. Storage-pressure and live Cloudflare Access remain activation checks.
