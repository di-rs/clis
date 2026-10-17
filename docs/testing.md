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
the draft override was enabled. Pushes to `master`, schedules and runtime release
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
The Gitmoji job keeps its required `git-policy` check name so existing branch
protection and other open PRs continue to work. Its workflow and steps have
descriptive emoji names.

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

The root pytest configuration registers the shared [dependency fixtures](../tools/boxie/tests/support/boxie_test_support/dependencies.py)
once. PostgreSQL and Temporal servers start only when requested and are shared
within the session; roles and databases remain isolated per test, with separate
teardown for each acquired resource. Request fixtures by name rather than importing
them into test modules. Keep reusable setup in support modules, use small Protocols
for checked fakes, and inject clocks and transports. Reserve monkeypatching for
environment handling and deliberate failures at system boundaries.

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
checks target `master`. Benchmark selection runs only affected or explicitly
requested suites. Privileged publication and deployment retain their trusted
default-branch boundaries.

Boxie has only the PostgreSQL/Temporal lifecycle. Default pytest excludes live
services; explicit integration selection fails when dependencies are missing.
Run the isolated infrastructure tier (including SQL/Temporal observability, migration and history/privacy checks) with:

```sh
uv run --locked python tools/boxie/tests/run_integration.py
```

The [Linux lifecycle workflow](../.github/workflows/boxie-lifecycle.yml) runs real
PostgreSQL/Temporal checks, pinned replay/cancellation, credential permissions and
endpoint restart checks on pull requests. The native lifecycle/browser E2Es are
temporarily manual-only while their hang is investigated. Use `workflow_dispatch`
to build the native images and `echor` and run all six cases, or run locally:

```sh
uv run --locked python tools/boxie/tests/run_integration.py --runtime runsc --browser -- -vv --durations=10
```

Each CI tier has its own timeout and reports individual tests and slow durations.
The optional native E2Es require Linux amd64 Docker with configured gVisor,
built `boxie:lifecycle`, `cli-playground:lifecycle`
and `clis-playground-sandbox:lifecycle` images, `target/release/echor`, and Chromium.
`BOXIE_NATIVE_BINARY` may select the native executable. The fixture owns random
Compose projects, volumes, identity labels and fault proxies. It independently
kills API, orchestrator (including its embedded workers) and Temporal; tests failed
Docker deletion, actual artifact COMMIT acknowledgment loss, collector overlap and journal failure across
orchestrator restart; and replays production histories. Cleanup removes only verified
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
uv run --locked pytest -m integration tools/boxie/tests/test_observability_native.py -k native_configs
```

The full native file requires a Linux Docker host with persistent journald and
real PostgreSQL/Temporal fixtures. Missing prerequisites fail when explicitly
selected. Storage-pressure and live Cloudflare Access remain activation checks.

## Component release and host checks

The root Python checks include host resource ownership, Pulumi component
mock graphs, single-host composition, private release preparation and the
standalone SSH activation adapter. Host-side scripts are checked against Python
3.12 grammar; application code remains on Python 3.15. No cloud resources are
created by these checks.

The PR-only [component release smoke](../.github/workflows/component-release-check.yml)
executes real Compose updates and failed-activation rollback while preserving a
random persistent sentinel and the exact container of an unrelated component:

```sh
uv run --locked pytest tools/deploy/tests/test_native_release.py -m integration
```

It owns random labeled test networks, volumes and containers. Missing Docker or
registry access fails when selected. This test does not establish cloud bootstrap,
production dependency readiness, native gVisor isolation or live DNS/Access.
Those acceptance gates remain in the
[single-host runbook](../infra/environments/single-host/README.md).
