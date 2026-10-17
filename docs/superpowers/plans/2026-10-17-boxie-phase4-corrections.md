# Boxie Phase 4 Corrections Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans for inline execution, or superpowers:subagent-driven-development if the user selects delegation. Steps use checkbox (`- [ ]`) syntax for tracking. This document does not start implementation.

**Execution:** The user requested implementation in PR42 on 2026-10-17. Follow the [delivery/evidence record](boxie-delivery/pr4/README.md) for completed changes and pending environment gates.

**Goal:** Complete the still-relevant Phase 4 outcomes without undoing the accepted changes from phases 1–3.

**Architecture:** Preserve the two-process Boxie service and its native supporting-service infrastructure. A small shared host package and thin single-host environment wire owners together; generic application releases and product-owned preview operations remain separate.

**Tech Stack:** Python 3.15, root uv workspace/lockfile, SQLModel/Alembic, temporalio 1.34.0, Pulumi, SSH, Docker Compose, native observability tools and GitHub Actions. Remote host adapter supports Python 3.12.

**Spec:** [Reconciled design, evidence and amended review](../specs/2026-10-17-boxie-phase4-reconciliation-design.md). Read this before the historical Phase 4 plan. Review baseline is PR42 `c9e43ac`; master baseline is `0883bde`.

## Global Constraints

- Planning only until the user reviews these artifacts and selects execution. Continue the existing `refactor/boxie-deployment` PR against master; do not open a fifth design PR.
- No deployment, paid provisioning or merge is implied. Check for real deployments before relying on the current no-migration assumption; preserve user changes and committed Alembic revisions.
- API and orchestrator are the only Boxie application processes. The orchestrator embeds SDK workers and alone receives Docker access. No legacy agent, standalone worker or custom retry/scheduling engine.
- PostgreSQL, Temporal, observability and operator ingress stay under `tools/boxie/infra`. No separate telemetry Python distributions, configuration compiler or speculative consumer telemetry.
- Shared host/network/storage preparation is infrastructure-only. Owners declare their directories/volumes and numeric readers; every shared resource has one external owner.
- Base Boxie/Temporal manifests work without monitoring. Selected production monitoring uses owner overlays. Grafana/Temporal UI remain private behind Cloudflare Access.
- Keep Grafana's 256 MiB hard cap, VictoriaLogs' 256 MiB cleanup target/newest-two-day exception, current log/metric privacy and bounded buffers. Preserve eight slots and the existing 60-minute/10-minute/two-minute/90-second policies.
- Default SSH ceiling 900 seconds, remote margin 30 seconds, accepted CLI budgets 60–900 seconds. No schema rollback, volume deletion or PTY restoration on application rollback.
- E2E tests prove behavior. Unit tests only cover distinct boundaries; reuse existing fixtures and avoid duplicate matrices. Missing explicitly selected integration prerequisites fail with an actionable diagnostic.
- Keep root Ruff/strict mypy/uv checks, independent service packaging, protected infrastructure review, draft CI opt-in and manual native/browser gates. Benchmarks remain PR-only and affected/requested; no performance claim from a smoke run.
- No backup implementation, HA, multi-host scheduling, job API or unrelated dependency upgrades. Record activation obligations, including backup/restore issue #38, separately.

## Review Focus

1. A cancellation during a mutating command must not free the deployment lock while owned commands survive: Task 3.
2. A valid SHA-shaped string or dirty working copy must not impersonate committed configuration: Task 2.
3. An atomic profile/credential replacement must actually become visible to its reader, while old snapshots remain valid: Tasks 2 and 4.
4. Same-SHA close/reopen and malicious ZIP metadata must fail before stale publication or unbounded allocation: Tasks 5 and 6.
5. Optional monitoring and product selection must survive the assembled install, and updates must not touch unrelated containers/state: Tasks 1, 7 and 8.

## File ownership and execution order

Tasks are sequential reviewable changes, not eight new subsystems. Execute
1 → 2 → 3 → 4 → 5 → 6 → 7 → 8. Keep meaningful existing tests and checks.
After each task, run its focused checks, inspect the diff and commit with the
repository's Gitmoji skill. Install normal hooks first; never bypass them.

| Files | Responsibility |
| --- | --- |
| `infra/host/host_infra/{host,contracts,resources}.py` | Existing host logic moved from `infra/platform`; typed inert handles and explicit resource preparation. |
| `tools/boxie/infra/boxie_infra/component.py`, `access.py` | Boxie-owned declarations; Access cloud resources output audiences/references for native assets. |
| `tools/boxie/infra/{postgres,temporal,observability,ingress}/` | One canonical native definition and operation contract per supporting service. |
| `infra/environments/single-host/environment.py` | Select owners, wire handles, produce ordered install inputs; no application internals or generated rule policy. |
| `tools/deploy/deploy_tool/{manifests,prepare,transport,remote}.py`, new `source.py` | Generic validation, committed-source selection, immutable public identity, private inputs and bounded activation. |
| `tools/boxie/infra/boxie_infra/profiles.py` | Small operator-only profile update command using current registry/SIGHUP behavior. |
| `tools/cli-playground/src/playground/preview/{build,github,publication,lifecycle,cli}.py` | Split existing product CI by behavior; no class per helper or duplicate archive parser. |
| `tools/cli-playground/src/playground/{storage,models,session_store,service,routes,app,artifacts}.py` | Durable publication generation, single-writer ownership and bounded archive parsing. |
| Existing deployment/lifecycle/packaging tests and workflows | Focused behavior and assembled integration; no replacement test framework. |

### Task 1: Restore current ownership and native configuration

**Covers:** R6, R7, monitoring part of R10.

**Files:** Move `infra/platform` host/bootstrap/contracts/resources/tests to
`infra/host` and namespace `host_infra`. Delete its duplicate `compose.yml` and
`init-databases.sql`; keep the existing Boxie PostgreSQL files. Move only required
cloud resource declarations from `infra/ingress/ingress_infra/access.py` to
`tools/boxie/infra/boxie_infra/access.py`; delete root ingress/observability
distributions and renderer. Modify Boxie's supporting-service native files,
production overlays, `infra/environments/single-host/{environment.py,tests/test_composition.py}`,
root `pyproject.toml`/`uv.lock`, runtime Dockerfile metadata copies and owner READMEs.

**Interfaces:** Keep `HostConfig`, `Host`, `HostHandle`, `NetworkHandles` and
`ResourcePlan` behavior under `host_infra`. `EnvironmentConfig` gains
`monitoring: bool = False`; existing optional `playground` and `operators` remain.
Selecting `operators` requires monitoring because it declares Grafana as well as
Temporal UI. `Boxie` declares its storage requests; environment aggregates them
and passes them to `ResourcePlan`, without defining DB roles or queries.

- [ ] Add one composition/packaging regression per ownership boundary: no root telemetry distributions; one PostgreSQL owner; exactly API/orchestrator; disabled Playground needs no product secrets; monitoring-disabled base deployment has no metrics requirements; enabled overlays resolve external volumes and both backing mounts.
- [ ] Run `uv run --locked pytest infra/environments/single-host/tests tools/boxie/tests/test_observability_config.py tools/boxie/tests/test_packaging.py`; observe the new assertions fail on PR42's current layout.
- [ ] Apply the moves/deletions and update declared dependencies with `uv lock`. Keep native `envsubst` for the three Prometheus and six operator variables. Put `/` and `/srv/boxie` selectors directly in owner-maintained rules/config; preserve latest dashboard query fixes and native pins. No arbitrary `consumer_scrapes` renderer.
- [ ] Use owner-maintained Compose production overrides for external storage and selected monitoring. Keep host resource creation separate; allow the tunnel's own egress network to stay component-owned. Verify protected storage and secret-free connection outputs still hold.
- [ ] Update obsolete migration/worker/platform documentation, run focused tests plus `uv run --locked ruff check .`, `uv run --locked mypy` and native config validation from Task 8; commit `♻️ Restore Boxie infrastructure ownership`.

### Task 2: Bind release preparation to committed source

**Covers:** R2, provenance portion of R8 and the original preparation hardening.

**Files:** Create `tools/deploy/deploy_tool/source.py`; modify existing manifest,
prepare and CLI modules, application release descriptors/examples,
`tools/deploy/tests/deploy_checks/test_cli.py`, and `tools/deploy/README.md`.

**Interfaces:** `source.committed_files(repository: Path, revision: str,
paths: tuple[str, ...]) -> dict[str, bytes]` resolves a commit and reads regular
Git blobs. CLI `prepare` accepts `--repository` and a repository-relative
`--descriptor`; the descriptor and selected files come from that commit.
`ReleaseIdentity` is a frozen dataclass in `manifests.py`: `component: str`,
`revision: str`, `images: dict[str, str]`, `configuration_hashes: dict[str, str]`,
`compatibility: dict[str, str]` and `settings: dict[str, str]` (including versioned
credential references). Canonical serialization supplies `release_id` and excludes private values.
Keep private inputs separate from the publishable manifest/archive and mode 0600.
Add `manifest --repository PATH --descriptor RELATIVE_PATH --revision SHA
--images JSON_PATH --settings JSON_PATH --output PATH`; it derives committed
configuration hashes rather than trusting caller-supplied hashes. `prepare`
recomputes and compares that manifest before adding separately supplied private
values. Task 7 uses this serializer.

- [ ] Add subprocess cases for nonexistent revision, descriptor/source revision mismatch, dirty/untracked files, committed symlinks, duplicate JSON keys, metadata over 64 KiB, unknown secret names and changed image digest at the same revision. Assert rejection before SSH and no credential bytes in public output or files.
- [ ] Run `uv run --locked pytest tools/deploy/tests/deploy_checks/test_cli.py` and observe the new provenance/metadata failures.
- [ ] Implement Git-backed selection with fixed argv, normalized owned paths and bounded reads. A dirty working copy is ignored for source files; its changed bytes must never enter the release. Require registry-qualified image digests and preserve current per-file/aggregate limits.
- [ ] Add descriptor `secret_environment` names, disjoint from nonsecret settings and image variables. Only declared private values may be injected. Include a nonsecret credential version/reference in public identity; a changed private payload at an existing immutable release ID is rejected rather than overwriting it. Rotation requires a new reference and targeted recreation, never an in-place file mutation presented as immutable rollback.
- [ ] Test that image/config/reference changes change identity, JSON ordering does not, private bytes do not leak, and retained releases resolve their recorded versioned paths. Run focused checks and commit `🔒 Bind release inputs to committed source`.

### Task 3: Make activation cancellation bounded and observable

**Covers:** R1 and M1; retain existing successful activation/rollback behavior.

**Files:** Modify `tools/deploy/deploy_tool/{remote,transport,__main__}.py`,
`tools/deploy/tests/deploy_checks/test_activation.py`, transport cases in
`test_cli.py` and the release runbook.

**Interfaces:** Preserve `Activator.run`, `activate` and result shape. Add an
internal cancellation exception/flag handled outside signal callbacks; SIGTERM
and SIGHUP prevent new commands and trigger bounded cleanup before lock release.
Interrupted activation records `recovery-required` when it cannot verify state;
transport treats it as failure. Keep Python 3.12 compatibility.

- [ ] Add a real subprocess test that starts a descendant, sends SIGTERM and SIGHUP separately, checks the child is gone and a competing activation cannot mutate before cleanup completes. Add a real SSH-disconnect fixture in the Linux integration tier; exception-only tests do not establish disconnect behavior.
- [ ] Run `uv run --locked pytest tools/deploy/tests/deploy_checks/test_activation.py`; confirm the signal test reproduces the surviving-child defect.
- [ ] Install signal handling around the activation lifetime. Kill/reap owned groups on cancellation, suppress secret output and retain pending status. Do not start rollback after cancellation unless its explicit remaining budget and connection-independent supervisor guarantee completion; uncertain state remains recovery-required.
- [ ] Bound SSH stdout/stderr while the process runs, not after `capture_output` completes. Validate `60 <= timeout <= 900` before SSH and subtract the 30-second remote margin. Keep existing command, health and rollback limits bounded by one deadline; reject at 59, accept at 60 and 900, reject at 901.
- [ ] Put `clis.release-id: ${CLIS_RELEASE_ID}` on each application service and require the expected label alongside immutable image and dependency readiness. Test a healthy same-image container with an old release label; it must not be promoted. Update real/fake release fixtures to exercise this contract.
- [ ] Test held lock, health stall, output flood, hook cancellation, partial startup, rollback failure and first activation with no previous release. Reuse the existing fake Docker matrix; add only missing cases. Document SIGKILL/host-loss/daemon-side-effect uncertainty and next-run inspection. Run checks and commit `🐛 Bound activation cancellation and cleanup`.

### Task 4: Add the separate approved-profile operation

**Covers:** R3.

**Files:** Create `tools/boxie/infra/boxie_infra/profiles.py` and
`tools/boxie/tests/test_profile_release.py`; modify production Compose,
environment examples, existing profile/config tests and owner README. Add a
protected manual profile workflow in Task 7.

**Interfaces:** `update_profiles(directory: Path, content: bytes,
signal_reload: Callable[[], None], read_digest: Callable[[], str],
timeout: float = 30) -> str`. Directory is the stable read-only mounted source;
replace only its `profiles.json` child. CLI resolves the exact installation/host
and orchestrator container before signalling. `read_digest` uses the existing
authenticated `/v1/profiles/resolve` response for a valid approved profile/command
and compares `config_digest`; do not add a second registry or control service.

- [ ] Add an E2E case: an existing request keeps its old snapshot, atomic update plus SIGHUP changes new resolution, invalid input preserves current approvals, and absent acknowledgement reports failure. Assert API/orchestrator container IDs remain unchanged.
- [ ] Run `uv run --locked pytest tools/boxie/tests/test_profile_release.py` and establish the absent operation/file-bind failure.
- [ ] Validate through the existing profile schema in the approved Boxie image, use a dedicated root-owned source directory readable by UID 10001, stage/fsync/rename its child and signal only the verified owner. Keep mutable source separate from retained immutable runtime profile versions.
- [ ] Observe the acknowledged digest within 30 seconds. On failure restore the prior source atomically and verify its acknowledgement; report recovery-required if restoration is uncertain. Never infer success from signal delivery alone.
- [ ] Run the focused regression and native bind-mount check, update the runbook and commit `✨ Add verified operator profile updates`.

### Task 5: Decompose preview CI and fence publication generations

**Covers:** R4 and preview portion of R8.

**Files:** Move behavior from `tools/cli-playground/preview_ci.py` into the five
`src/playground/preview` modules listed above; remove the script after workflow
callers migrate. Modify product storage/models/session_store/service/routes/app,
existing preview/API/service tests and preview/build workflows. Package the
already-shared `tools/ci` transport as `clis-ci` with module `ci`; declare it as a
CI-only optional dependency for Playground and cli-bench, not a new runtime
service or duplicate transport implementation. Update root lockfile/packaging.

**Interfaces:** Preserve existing `build`, `publish`, `current_pull`, `update_build`
and `lifecycle` operations with injected GitHub/gateway boundaries. Module CLI is
`python -m playground.preview.cli`, with explicit `--repository` for source-tree
operations. No installed-package-relative repository discovery or `sys.path` edits.
Product `LifecycleState(generation: int, closed: bool)` lives outside removable
preview data. Authenticated `GET /api/pull-requests/{pr}/lifecycle` returns it.
Uploads/build writes require `expected_generation`; acknowledgements include it.
Stale generation returns HTTP 409 with detail `stale lifecycle`.

- [ ] Add deterministic tests holding transfer before intent creation and during Boxie upload. Deliver close/reopen at the same SHA, release transfer and assert no stale metadata/ACK/GitHub success. Add restart/corrupt-state, 429-retry-frozen-generation and wrong-ACK cases. Preserve existing cancellation/run/attempt/head tests.
- [ ] Run `uv run --locked pytest tools/cli-playground/tests/test_preview_ci.py tools/cli-playground/tests/test_service.py tools/cli-playground/tests/test_api.py`; establish failures before changing behavior.
- [ ] Enforce one writer with an OS-held nonblocking data-directory lock for application lifespan. Atomically persist lifecycle state; close and reopen both advance generation before waiting on upload serialization. Reopen stays closed until its own guarded cleanup completes. Older cleanup cannot reopen/remove a newer generation.
- [ ] Persist the captured generation in preview records and upload intents. Check it before receive, after staging, on fast paths/recovery and synchronously at metadata commit. Do not refresh it on retry; keep immutable revision/build ordering and artifact retirement intact.
- [ ] Split controller responsibilities without changing transport limits/provenance policy. Capture one open generation before transfer; verify ACK and generation again before GitHub writes. Document the observed-event boundary and unavoidable cross-system race; do not claim detection of undelivered GitHub events.
- [ ] Test installed preview CLI outside the checkout, shared transport consumers, protected default-branch publication and read-only PR builds. Run focused checks and commit `♻️ Separate preview operations and fence stale publication`.

### Task 6: Reject oversized archive metadata before parsing

**Covers:** R5.

**Files:** Modify `tools/cli-playground/src/playground/artifacts.py`; create
`tools/cli-playground/tests/test_artifact_metadata.py` and use existing API fixtures.

**Interfaces:** `validate_zip_directory(source: BinaryIO) -> None`, called before
`ZipFile` by both byte-buffer and staged-file paths. Preserve the source position.
Accept the existing GitHub/Python ZIP producers, including valid ZIP64 records;
reject multi-disk and inconsistent metadata. Limit actual directory bytes to
8 MiB, actual entries to `MAX_CLIS` (1024), names to the existing 64-byte ASCII
policy, and per-entry extra/comment fields to 4 KiB each. These are proposed
resource bounds for review, not claims that the old plan specified them.

- [ ] Construct tiny malformed fixtures with excessive declared counts, repeated directory records despite a small EOCD count, excessive metadata lengths, truncation and ZIP64 size/count inconsistencies. Spy on `ZipFile` only to assert it is never reached for rejected input; retain valid producer archives as positive cases.
- [ ] Run `uv run --locked pytest tools/cli-playground/tests/test_artifact_metadata.py` and observe the preallocation assertion fail.
- [ ] Scan bounded EOCD/ZIP64 metadata and directory headers incrementally, checking offsets/lengths against file size and counting actual records. Allocate no attacker-sized directory buffer or member list. Reuse existing ELF/content/path validation afterward.
- [ ] Run the new tests plus existing artifact/API/preview cases, keep fixtures small and hand-derived, then commit `🔒 Bound preview ZIP metadata before allocation`.

### Task 7: Connect owner operations and trusted workflows

**Covers:** supporting-service part of R8 and corrected installation/runbook claims.

**Files:** Modify single-host README/environment outputs, all four Boxie supporting
service READMEs, application release workflow and its two callers, protected
infrastructure setup/review workflows, owner workflow tests. Create
`.github/workflows/boxie-supporting-release.yml` and
`.github/workflows/boxie-profiles-release.yml` as thin manual callers of fixed
owner commands. Update docs/testing and the PR4 delivery record.

**Interfaces:** Supporting release input is a finite unit choice
`postgres | temporal | observability | ingress` and a reviewed immutable revision.
Each unit has one canonical native Compose base/production override and explicit
image/config/readiness contract. Ordinary native activation excludes schema/init
jobs. Profile workflow invokes Task 4 with reviewed profile bytes/digest.
Provisioning alone receives cloud/state credentials; application release receives
connection profile, SSH credential and that application's private inputs.

- [ ] Extend workflow boundary tests: application cannot run schemas/profiles/Pulumi; supporting choice maps to fixed owner paths; reviewed source is resolved before protected credentials; draft/label and manual native gates remain. Do not assert YAML layout instead of executed shell behavior where recording boundaries already exist.
- [ ] Run `uv run --locked pytest tools/cli-playground/tests/test_infra_workflows.py tools/boxie/tests/test_release_hook.py` and new owner-operation cases; record missing contracts.
- [ ] Serialize public release manifests through the shared CLI rather than inline duplicate logic. Record supporting native versions/config hashes and selected services, with separate schema approval instructions. Adapt image pull/preflight/readiness to upstream tools; do not invent a universal rollback promise for database/server upgrades.
- [ ] Preserve PR48 protected Pulumi preview/drift semantics and shared-network ownership. Keep app auto-deploy opt-in, credentials per protected environment and fixed concurrency keys. Do not publish images or run privileged workflows during implementation without authorization.
- [ ] Document fresh order: host/storage → Boxie PostgreSQL → explicit Alembic/Temporal schemas → Temporal/selected monitoring/Access → Boxie generic smoke → optional Playground. Explain each reader UID, external secret path, immutable rotation and rollback limit. Remove obsolete numbered migration and three-process descriptions.
- [ ] Run `actionlint`, `zizmor --offline --no-progress .github/workflows`, owner checks and documentation hooks; commit `♻️ Connect independently owned deployment operations`.

### Task 8: Prove assembled behavior and reconcile final evidence

**Covers:** R9, completion portion of R10 and acceptance of all earlier tasks.

**Files:** Extend `tools/boxie/tests/integration/stack.py`, create
`tools/boxie/tests/integration/test_deployment.py`, extend the existing
`tools/boxie/tests/run_integration.py` and `.github/workflows/boxie-lifecycle.yml`;
retain `tools/deploy/tests/test_native_release.py` for its distinct generic engine
coverage. Update packaging tests, docs/testing and PR4 delivery record.

**Interfaces:** Existing `LifecycleStack` is extended to activate owner manifests
through the corrected deployment path, with recorded random resource ownership.
`run_integration.py --deployment` selects the compact assembled scenario;
`--capacity` requires Linux amd64/runsc and selects the bounded eight-sandbox
exercise. Both use existing diagnostics, dependency checks and cleanup.

- [ ] Add one assembled scenario starting without Playground, running explicit schemas/bootstrap and generic create/terminal/stop, then adding Playground and verifying publication/terminal flow. Check private network reachability and actual UID/read permissions, not only resolved YAML.
- [ ] Within that fixture, record real container IDs: a Playground update/failed rollback preserves Boxie, Temporal, PostgreSQL and monitoring IDs/data. A Boxie update explicitly ends terminals and preserves cleanup accounting. Include the profile acknowledgement/old-snapshot case and dependency outage recovery without duplicating the whole lifecycle matrix.
- [ ] Run `uv run --locked python tools/boxie/tests/run_integration.py --deployment` on a suitable disposable host; failures should identify unmet composition/permission contracts. Implement only integration adapters needed to exercise production commands, then rerun covering cases.
- [ ] Add an opt-in 120-second capacity observation with eight active bounded sandboxes, representative sanitized logs and terminal activity. Record host CPU/memory/disk, cgroup pressure/OOM, cleanup and telemetry loss. A disposable CI machine proves harness behavior, not selected production VM sizing. No automatic resizing or cap increase.
- [ ] Run the appropriate final matrix below. Record exact commit, command, environment, pass/fail/pending and cleanup for each tier. Do not carry old phase3 evidence forward where config/topology changed or claim skipped draft CI passed.
- [ ] Self-review the full diff against the reconciled spec and R1–R10/M1; obtain one whole-change review when the user-selected execution method permits it. Fix material findings with focused regressions, refresh PR42's description/delivery record, and commit `🧪 Verify composed Boxie deployment contracts`. Push only within implementation authorization; do not merge or activate production.

## Final verification matrix

| Tier | Commands/evidence | Limit |
| --- | --- | --- |
| Python | `uv sync --locked --all-packages`; `uv run --locked ruff check .`; `uv run --locked ruff format --check .`; `uv run --locked mypy`; `uv run --locked pytest` | Root locked workspace; no service starts in ordinary tests. |
| Generic release | `uv run --locked pytest tools/deploy/tests/test_native_release.py -m integration` | Real Compose fixture, not complete production proof. |
| Packaging/UI | `pnpm --dir tools/cli-playground/frontend build`; `uv run --locked pytest tools/boxie/tests/test_packaging.py -m integration`; existing frontend browser workflow | Services install independently; no workspace imports in runtime images. |
| Native configs | `uv run --locked pytest -m integration tools/boxie/tests/test_observability_native.py -k native_configs` | Pinned native binaries validate owner assets; no live Access claim. |
| SQL/Temporal | `uv run --locked python tools/boxie/tests/run_integration.py` | Existing real dependency checks; no duplicate harness. |
| Assembled release | Task 8 `--deployment` scenario, Linux SSH-disconnect case | Must exercise actual new production ownership/permissions. |
| Native runtime/browser | `uv run --locked python tools/boxie/tests/run_integration.py --runtime runsc --browser -- -vv --durations=10` | Existing manual tier; known hang remains explicit until diagnosed, no runc fallback. |
| Workflow/docs | `actionlint`; `zizmor --offline --no-progress .github/workflows`; `git diff --check`; `prek run lychee --files CHANGED_MARKDOWN`; `prek run typos --files CHANGED_MARKDOWN` | Use actual changed paths, including newly tracked plan/spec. |
| Activation only | Selected-host provider preview, reboot/missing disk, DNS/ACME/Access/bypass denial, eight-sandbox headroom, backup/restore disposition | Requires authorized environment; remain pending in implementation review if unavailable. |

No Rust CLI behavior is planned. If implementation touches CLI tests/fixtures,
apply the repository `cli-testing` skill and independent-reference rules. Do not
run unrelated Rust benchmarks or use benchmark jobs as deployment verification.

## Plan coverage and handoff

R1→3; R2→2; R3→4; R4→5; R5→6; R6/R7→1; R8→5/7;
R9→8; R10→1/8; M1→3. Current ownership, process/schema, security, release and
verification requirements map to the global constraints and tasks above.

Recommended execution: inline, in the current PR branch, with one final independent
whole-change review. The tasks share owner manifests and deployment contracts;
serial execution avoids competing edits and repeated test machinery. Delegation
is an option only if the user selects it. Review the reconciled design and plan
before starting; no implementation work has been performed by creating them.
