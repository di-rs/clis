# Boxie deployment implementation plan

> **For agentic workers:** Use executing-plans for integration and dispatching-parallel-agents for the independent platform and release-tool tasks. Steps use checkbox syntax for tracking.

**Goal:** Complete phase 4 of the agreed Boxie stack with independent, bounded component releases and an explicit fresh-install path.

**Architecture:** Platform owns the host, storage and shared resource handles. A thin single-host Pulumi project connects service-owned declarations. Generic release tooling accepts service-owned descriptors, immutable release manifests and a nonsecret pinned SSH connection profile; it never reads Pulumi state.

**Tech stack:** Python 3.15, root uv workspace, Pulumi, Docker Compose, SSH, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-15-boxie-design.md`.

## Global constraints

- Stack this PR on `feat/boxie-observability`; do not merge or deploy.
- Preserve one owner per shared network, persistent volume and ingress process.
- Ordinary releases reconcile only the selected component; schema jobs are explicit installation/upgrade steps.
- Use immutable images, pinned SSH host identity, private credential files, bounded commands and truthful rollback results.
- Keep Grafana's 256 MiB hard RAM limit and VictoriaLogs' 256 MiB cleanup target; the newest two daily partitions may exceed that target.
- No migration, full backup system, multi-host placement or PTY handover.

## Review focus

- A failed first activation must not advertise a successful rollback or remove persistent data.
- An ordinary Playground release must not access provisioning credentials or update Boxie/Temporal.
- A missing persistent disk must stop Docker before application writes reach the host root filesystem.
- Invalid manifest paths, environment interpolation and mutable image references must fail before SSH.
- Boxie orchestrator updates must explicitly end or drain sessions and preserve ownership accounting.

### Task 1: Shared platform and environment composition

- [x] Add Pulumi mock tests for private exposure, optional Playground, pinned public connection outputs and protected storage.
- [x] Run the new tests and observe missing implementation failures.
- [x] Move platform provisioning/bootstrap ownership and add service-local component contracts plus thin environment composition.
- [x] Run focused tests, Ruff and strict mypy; record cloud preview limitations.

### Task 2: Generic release engine

- [x] Add executable CLI/activation tests covering immutable inputs, targeted updates, health failures and rollback.
- [x] Observe failures before adding manifest/preparation/transport/activation modules.
- [x] Implement service-owned descriptors and bounded transport with independent connection credentials.
- [x] Verify tests with local process boundaries; no live host mutations.

### Task 3: Service activation and CI integration

- [x] Test service health/release identity and safe orchestrator update behavior.
- [x] Add healthchecks, explicit schema/install operations, component release descriptors and independent trusted workflows.
- [x] Replace obsolete activation guards and state-dependent release paths; preserve trusted preview publication boundaries.
- [x] Register packages, update lockfile and runtime build inputs, and document the operator install/update/failure procedures.

### Task 4: Integrated verification and next PR

- [x] Run root Python checks, affected frontend/workflow/Markdown checks and Compose validation.
- [x] Request an independent whole-branch review and fix material findings with regression tests.
- [ ] Record evidence and activation gates, commit with Gitmoji, push and open a draft stacked PR targeting phase 3.
