# Phase 4 reconciliation and correction design

Status: proposed correction design and requirement audit, requested on 2026-10-17.
Planning only; no implementation, deployment, merge, or new design PR is authorized
by this document. Review target: PR #42 at
`c9e43acaf3ce776d0c2f128befee341310881c99`; merged baseline:
`0883bde71f1c206846ea7a35e57e5be2c9929bcc`.

## Outcome

Phase 4 remains necessary: master deliberately disables production activation,
and the service owners still need shared host composition, independent releases,
failure handling and a verified installation procedure. The original Phase 4
plan is not an authoritative implementation recipe after the revisions to
phases 1–3. Complete its surviving outcomes against the merged architecture.

The user wants maintainable service ownership, fewer layers, upstream tooling,
and focused behavioral tests. Do not restore removed workers, shared telemetry
packages, custom schema management, configuration compilers, or extensive test
frameworks to satisfy historical task names.

## Evidence and precedence

Latest explicit user instructions take precedence over earlier plans. Approved
revision specs and merged code establish the current contract. Old controller
rulings and handoff proposals identify concerns but are not fresh user approval.
PR42's replacement plan and delivery claims cannot silently supersede the user.

| Evidence | Decision carried forward |
| --- | --- |
| [Phase 1 review](codex://threads/01a14563-b47b-7752-be83-a2c61b54ac11) | Use orchestrator terminology; simplify CI and oversized modules. |
| [Infrastructure ownership conversation](codex://threads/01a14646-2785-7de2-b75e-defcd629a3bf) | Explicit user instruction: “put postgres under boxie then and dont call in platform”; confirmed `boxie/infra/postgres`. |
| [ORM design](2026-10-16-boxie-orm-design.md) | SQLModel/SQLAlchemy and Alembic replace application raw SQL and the custom migration ledger. Upstream Temporal schemas retain their own tooling. |
| [Embedded Temporal design](2026-10-16-boxie-temporal-integration-design.md) and [approval](codex://threads/01a1461c-4476-7191-ad54-039f6eea986a) | Exactly two application processes: API and orchestrator; embedded lifecycle/maintenance SDK workers, local safety and physical ownership. |
| [Phase 3 ownership request](codex://threads/01a1469a-b104-7ff0-9a0b-d86db93df59f) | Boxie self-contained; do not extract telemetry for disposable Playground environments. |
| [Phase 3 simplification request](codex://threads/01a14908-8770-7de1-828f-97456ba7f19f) | Native defaults and upstream tools, compact tests, no separate observability workflow; Grafana and Temporal UI still require Cloudflare Access. |
| [Merged design update](2026-10-15-boxie-design.md#pr-41-integration-update-2026-10-17) | Boxie owns telemetry code and observability/ingress assets; supersedes the early shared ownership diagram. |
| [Protected infrastructure review](codex://threads/01a1465c-4bdc-7110-bfa3-640498882d49), merged PR #48 | Shared networks externally owned; trusted reviewed revision before credentials, protected preview and opt-in drift checks. Backup work remains with later activation. |
| [Original Phase 4 discussion](codex://threads/01a13ee0-447e-70c1-90d4-d52ec1d3bd32) | Independent deployment, committed release provenance, bounded failure handling, approved profiles, preview ordering and fresh-install evidence remain useful outcomes. |

The original detailed plan is preserved in that chat's workspace at
`docs/superpowers/plans/2026-10-15-boxie-pr4-deployment.md`, with PR2 preflight,
credential-ownership and preview-fence handoffs under `boxie-delivery/pr2/`.
Those ignored local records predate the later revisions. The surviving decisions
needed for execution are restated here, so execution does not require those files.

## Requirement disposition

| Earlier requirement | Current disposition |
| --- | --- |
| Thin environment plus shared host/network/disk provisioning | Retain. Use `infra/host` to make the responsibility explicit. `platform` was a naming choice, not permission to own Boxie's database. |
| Root shared observability and ingress distributions | Superseded. Remove PR42's new distributions and runtime configuration renderer; preserve Boxie-owned native assets and minimal substitutions. |
| PostgreSQL under a root platform package | Superseded by explicit instruction. Preserve one definition under `tools/boxie/infra/postgres`. Host provisioning may prepare storage without owning database policy/schema. |
| Standalone worker/agent release and worker certificate | Superseded. API plus orchestrator share one application image/release; orchestrator has its own Temporal identity. |
| Numbered SQL migrations/checksum ledger | Superseded. Explicit Alembic job; no migration during ordinary startup or application rollback. |
| Independent telemetry lifecycle | Retain under Boxie ownership. Independent release does not require a Python distribution or a global owner. |
| Base Boxie/Temporal deployment requires monitoring inputs | Rejected by merged optional-overlay contract. Monitoring is selectable in the environment; production can select it without making base manifests depend on it. |
| All components must adopt one custom release descriptor framework | Adapt. Applications use the generic engine; supporting services may retain pinned native Compose commands and explicit operation profiles. Each still needs a documented, independently targeted release/verification contract. |
| Exactly 1200 seconds, 180-second reserve, 120-second lock wait | Historical plan settings, not current product requirements. Preserve PR42's 900-second ceiling and fail-fast locking, correct its boundary bug, and prove the total budget and cleanup. |
| Approved-profile update without restarting Boxie | Retain; the current runtime already supports SIGHUP and pinned snapshots. Add only the missing operator path and acknowledgement. |
| Split preview controller; invalidate stale transfer on close/reopen | Retain outcome. Use the current `src/playground` layout. Define the observable lifecycle boundary; do not promise atomic transactions with GitHub. |
| Bound ZIP directory allocations before `ZipFile` | Retain, independent of infrastructure revisions. |
| Separate observability fault workflow and large evidence framework | Superseded. Extend existing lifecycle checks with a compact integration scenario. Preserve manual native/browser gating while the known hang is unresolved. |
| Fresh assembled install and independent-update evidence | Retain; synthetic release smoke alone is insufficient. Reuse real service fixtures and owner commands. |
| Eight-sandbox capacity, reboot/disk failure, live DNS/Access | Required before production readiness. Harness coverage can be implemented now; selected-host measurements/live provider checks remain explicitly pending until authorized prerequisites exist. |
| Four stacked PRs targeting unmerged predecessors | Historical delivery topology. PR39–41 are merged; continue PR42 against master, no fifth design PR. |
| Backups, HA, multi-host placement, jobs, PTY restoration | Not silently added to this correction. Backup implementation/restore remains activation work and issue #38; jobs remain issue #37. |

## Revised review

The earlier chat review is amended as follows. R1–R2 are reproduced implementation
defects. R3–R5 are surviving planned hardening gaps, not newly introduced bugs.
R6–R7 are regressions against later accepted ownership requirements. R8–R10
clarify completion and evidence rather than asserting untested runtime failures.

| ID | Priority | Finding and evidence at the reviewed head | Correction |
| --- | --- | --- | --- |
| R1 | P1 | `tools/deploy/deploy_tool/remote.py:75–116`: SIGTERM ends the activator without executing its cleanup; a detached command survives. Reproduced with a sleeping child. A released lock can then allow overlapping mutations. | Signal-aware bounded cleanup; retain lock until owned children are reaped; test disconnect separately from ordinary exceptions. |
| R2 | P2 | `tools/deploy/deploy_tool/manifests.py:159–171`: reads working files and only syntax-checks the revision. Preparation accepted uncommitted configuration with a nonexistent revision. | Resolve a commit and select its declared files before any SSH; validate source-owned paths/metadata and public release identity. |
| R3 | P2 | Production binds `BOXIE_PROFILES_FILE` as a single file; no separate approved-profile operation exists. Atomic replacement does not update that bind mount. | Directory mount, atomic child replacement, SIGHUP and observed digest acknowledgement; retain old approvals/snapshots on failure. |
| R4 | P2 | `preview_ci.py` checks current open/head only; a same-SHA close/reopen can escape checks. Existing cancelled intents cover some processed closes, not all pre-intent/reopen races. | Product-owned durable generation captured before transfer; reject after observed invalidation and verify acknowledgements. |
| R5 | P2 | `src/playground/artifacts.py:52–63`: `ZipFile` parses central metadata before the member limit. | Bounded directory scan before construction in both publication paths. |
| R6 | P2 | New `infra/ingress` and `infra/observability` distributions split Boxie ownership and restore a Python configuration renderer removed by phase 3. The merged design explicitly supersedes this layout. | Remove these packages, retain native owner assets, keep necessary Access cloud declarations in `boxie_infra`. |
| R7 | P2 | `infra/platform/compose.yml`, `init-databases.sql` and README add a second PostgreSQL definition while Boxie's original remains. README restores obsolete numbered/checksummed migration claims. | One Boxie-owned PostgreSQL definition, external host storage handles, Alembic documentation. |
| R8 | P2 | The 652-line preview controller was not decomposed; supporting-service release contracts remain manual and incompletely connected. | Focused product modules and a small owner-operation inventory; no generic service framework. |
| R9 | P2 | New real Compose smoke exercises synthetic HTTP services, not assembled Boxie/Temporal/Playground. | One reused-fixture assembled install/update/failure gate; retain precise environmental limits. |
| R10 | P2 | Replacement plan marks broad completion without reconciling later instructions; production manifests also make metrics mandatory although merged base deployments are optional. | This reconciliation, optional monitoring selection, refreshed acceptance record. |
| M1 | P3 | Documented `deploy --timeout 30` passes a zero remote budget. | Reject unsupported small budgets before transport; test exact bounds. |

At c9e43ac, 31 focused deployment tests passed using Python 3.15 with the interpreter
also on PATH; the initial mixed-interpreter run failed one transport fixture.
The review reproductions are additional evidence, not committed regression tests.
Current-head GitHub test/security jobs were skipped. No fresh cloud, native
gVisor or combined-capacity verification was performed during this audit.

## Chosen correction and alternatives

Keep PR42's useful host and application-release work, but reconcile it with merged
owners. This has the smallest behavioral surface and preserves the user's
simplification decisions. Moving all service infrastructure to root would reverse
those decisions. Rebuilding the entire old Phase 4 architecture would restore
deleted machinery and duplicate evidence. Neither alternative is recommended.

Target ownership:

```text
infra/host/                         shared VM, firewall, disk, bootstrap, resource handles
infra/environments/single-host/     selection, wiring and ordered operator procedure
tools/boxie/infra/boxie_infra/       Boxie configuration and minimal cloud declarations
tools/boxie/infra/postgres/          database service, roles and native operations
tools/boxie/infra/temporal/          server, schema/bootstrap operations and UI
tools/boxie/infra/observability/     native collector/store/metrics/dashboard configuration
tools/boxie/infra/ingress/           operator tunnel and Access origin configuration
tools/cli-playground/infra/         product DNS, routes, image and release inputs
tools/cli-playground/src/playground/preview/  trusted product CI operations
tools/deploy/                      generic application release preparation and transport
```

Host preparation consumes explicit numeric directory ownership and external
network/volume declarations from the selected owners. It neither defines database
roles nor edits Prometheus queries. Cloudflare Access resource declarations belong
to existing `boxie_infra`; they output references/audiences, not an independently
maintained copy of native cloudflared configuration. Keep the six-variable native
substitution and fail-closed audience validation.

Use static owner-maintained production Compose overlays for external volumes and
optional monitoring. The fixed single-host mount list covers `/` and `/srv/boxie`
in native node-exporter filters, rules and promtool tests. Do not introduce
arbitrary consumer scrape targets or a configuration compiler. Shared bridges
have one owner; per-component private egress bridges may remain component-owned.

## Safety and acceptance contracts

- No automatic application release runs Pulumi, schema jobs, profile updates or
  another component's reconciliation. Schemas are explicit forward operations.
- Release identity includes committed configuration, immutable images,
  compatibility and nonsecret versioned references. Separate private credential
  transport/storage from public manifest/archive; never publish prepared secrets.
- Default SSH operation ceiling stays 900 seconds with a 30-second remote margin;
  supported CLI budgets become 60–900 seconds. Remote command/output/readiness
  bounds and cleanup fit the same deadline; interruption is never success.
- Handled SIGTERM/SIGHUP stops and reaps owned local children before unlocking.
  SIGKILL, host loss and Docker daemon side effects cannot be undone by Python;
  retained status and next-run inspection must report/reconcile uncertainty.
- Profile updates are a protected operator command. Ordinary Playground releases
  cannot change approvals. Existing requests retain their pinned snapshot.
- Preview generation guarantees rejection after a lifecycle invalidation reaches
  Playground before publication commits. GitHub state queries cannot detect an
  event that has not arrived, nor make GitHub writes atomic with product commits.
- Only one Playground writer owns a data directory. A nonblocking OS lock enforces
  this before serving; durable generation survives restart and corrupt state fails
  closed. No cross-process transaction framework is added.
- Retain 60-minute absolute, 10-minute idle, two-minute disconnect and 90-second
  control-lease policies; eight sandbox slots; no PTY restoration promise.
- Retain Python 3.15, temporalio 1.34.0, SQLModel/Alembic, pinned native images and
  root uv tooling. Host-side release code remains compatible with Python 3.12.
- Grafana hard RAM cap is 256 MiB. VictoriaLogs' 256 MiB cleanup target can be
  exceeded by protected newest-two-day partitions. Preserve current native
  journald/Vector/Prometheus bounds and best-effort loss semantics.
- Draft CI remains skipped unless explicitly selected; benchmarks remain PR-only,
  affected or explicitly requested. Do not restore old branch matrices or an
  observability-specific fault workflow.

Implementation completion requires focused failure tests, independent packaging,
native configuration validation, and a real assembled-stack deployment scenario
where prerequisites are available. Production activation additionally requires
provider preview, reboot/missing-disk proof, real DNS/ACME/Access/origin checks,
eight-sandbox measurements on the selected host, and the separately tracked backup
decision. A missing prerequisite is recorded as pending, never replaced by mocks.

See the [correction plan](../plans/2026-10-17-boxie-phase4-corrections.md).
