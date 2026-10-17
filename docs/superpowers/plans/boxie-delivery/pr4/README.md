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
