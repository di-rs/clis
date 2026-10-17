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
| Root locked Python suite | 693 passed; 123 integration cases excluded by the ordinary-test policy |
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
