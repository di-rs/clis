# Phase 4 implementation and acceptance record

PR42 follows the [reconciled requirements](../../../specs/2026-10-17-boxie-phase4-reconciliation-design.md)
and [correction plan](../../2026-10-17-boxie-phase4-corrections.md). PostgreSQL,
Temporal, observability and operator ingress remain Boxie-owned native assets;
`infra/host` prepares shared resources and the environment wires selected owners.

The correction includes committed-source/public release identity, bounded
cancellation and SSH output, release-label readiness, separately acknowledged
profile updates, durable preview generations and bounded ZIP metadata parsing.
Application, supporting-service, profile and infrastructure operations have
separate trusted workflows. Monitoring and Playground remain optional.

Task 8 acceptance is **pending**. The assembled Linux/runsc scenario must pass
through the actual supporting-release operation before the correction plan can
be called successfully executed. The earlier local prerequisite failures and
skipped draft CI do not satisfy that gate.

Focused tests, native configuration validation, real Compose release/rollback,
actual profile SIGHUP acknowledgement and directory-bind visibility have been
run locally. The final verification table is recorded after the assembled gate
implementation. No production credentials or provider-backed environment were
used. No live deployment, image publication, provisioning or merge is part of
this correction.

See the [single-host runbook](../../../../../infra/environments/single-host/README.md)
for the exact installation order, credential readers/rotation, manual owner
checkout, recovery, and pending production activation checks. Native Linux/runsc,
browser, selected-host capacity, reboot/disk failure, real DNS/Access and backup
restore evidence must not be inferred from local tests or skipped draft CI.

## Correction verification, 2026-10-17

| Check | Result/environment |
| --- | --- |
| Root locked Python suite after final review fixes | 699 passed; 123 integration cases excluded by the ordinary-test policy |
| PostgreSQL/Temporal and profile SIGHUP tier | 51 passed against disposable real services |
| Generic Compose release/rollback plus selected overlay | 1 passed; persistent sentinel and unrelated container preserved |
| Native Vector/Prometheus/rule/cloudflared validators | 1 passed against pinned upstream tools |
| Frontend | Built with pinned Node 26.11.1 and frozen pnpm lockfile |
| Independent runtime images | Both Dockerfiles built; isolated runtime imports passed without CI-only dependencies |
| Independent runtime wheel/sdist install, optional preview CLI and Compose topology | 10 passed outside the checkout |
| Real SSH disconnect | 1 passed on Linux/Python 3.15 in a disposable container; source mounted read-only, locked workspace |
| Root Ruff/format/strict mypy and workflow checks | Passed; required hooks run before every implementation commit |
| Assembled runsc scenario on this macOS arm64 host | Explicit prerequisite failure for both selected variants; capacity cases unselected |
| Native browser, selected-host capacity, provider/disk/reboot/DNS/Access/backup restore | Pending authorized suitable environment; no production readiness claim |

The assembled scenario and 120-second observation are implemented in the existing
lifecycle harness and separately selectable in the manual workflow. Their fixture
adaptations are recorded with evidence: owned local registry, loopback endpoints,
random resource/project names, external bind-backed volumes, and DNS/ACME disabled
in the proxy fixture. None establishes provider bootstrap or public activation.

All local disposable service fixtures completed their cleanup. The Linux SSH
container was automatically removed. Normal branch work is preserved in PR42;
no merge or production operation was requested or performed.

The independent review found three defects, each reproduced with a failing
regression before correction: an alternate ZIP end record could bypass the
metadata scan; a replayed close event could strand an open PR behind a closed
tombstone; and the assembled fixture retained an incompatible HTTPS preview
domain after selecting HTTP loopback. The fixes align ZIP footer selection with
the downstream reader, reconcile open state through a fresh generation and latest
build replay, and validate the fixture's adapted origin with the real gateway
settings parser. The focused review suite passed 84 tests; the full locked Python
suite passed 699 tests in 93.97 seconds. Lint, formatting and strict typing passed,
and the corrected Playground runtime image was rebuilt and imported in isolation.
No minor findings were deferred.

## PR42 review follow-up

Supporting releases now use the project name declared by each native manifest,
matching fresh installation: `boxie-postgres`, `boxie-temporal`,
`boxie-observability` and `boxie-operator-ingress`. Native Compose regressions
reproduced all four mismatches before the fix. A real PostgreSQL installation
followed by `activate_supporting` preserves the container ID and a persistent
data sentinel; its committed fixture source contains no administrator password.

The assembled fixture installs from committed adaptations of the owner assets,
then calls `activate_supporting` for PostgreSQL, Temporal and selected monitoring.
It verifies that unchanged releases retain every selected container ID and saves
the owner-operation results in public evidence. Schema setup and namespace
bootstrap still run explicitly before Temporal release readiness. The lifecycle
workflow also runs the native project and PostgreSQL transition regressions.

Native fixture regressions also preserve dynamic certificate and monitoring bind
mounts when merging Compose assets without committing private environment values.
All 699 ordinary Python tests passed on macOS arm64 (128 integration cases
excluded from that run). All seven native regressions passed against Docker
Desktop after the fixture correction, with both Compose v2.36.2 and v5.6.0.
Root Ruff, formatting, strict mypy, workflow lint/security and documentation hooks
passed. Assembled Linux/runsc verification is pending the updated manual workflow with
`deployment=true`; production activation and selected-host capacity remain
separate checks.

The [first Linux run](https://github.com/di-rs/clis/actions/runs/39058293451),
at `9522e8f`, passed the four project checks and PostgreSQL transition, then
stopped at two fixture bind-parsing failures in the runner's Compose version.
That compatibility failure was reproduced locally with Compose v2.36.2 and
corrected by declaring variable path binds before native merging. No assembled
pass is inferred from that failed attempt.
