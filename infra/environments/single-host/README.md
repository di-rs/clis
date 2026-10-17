# Install the single-host deployment

This Pulumi environment selects a shared host, Boxie, optional monitoring and
operator dashboards, and optional Playground. Boxie owns PostgreSQL, Temporal,
observability and ingress beside its code. Pulumi provisions cloud resources and
exports inert handles; it never starts Compose services or runs schema jobs.

Use the protected infrastructure review workflow with an explicitly reviewed
full commit SHA before requesting provisioning credentials. PR48's preview and
opt-in drift checks remain separate from deployment. The existing protected
`platform` environment name is retained for its approvals and credential store.
No provider-backed deployment was performed for this implementation.

## Select components and prepare the host

Copy [Pulumi.example.yaml](Pulumi.example.yaml) to an operator-owned stack config.
`monitoring` defaults to false; selecting `operators` requires it. Playground is
optional and an installation without it needs no product DNS or deployment token.
No monitoring inputs are required for the base Boxie/Temporal manifests.

After reviewing provider changes, use the manual setup workflow. Export the
public `connectionProfile`, `sharedResources`, and `deploymentHandles` separately
from the secret deployment key and tunnel credentials. Store the administrative
SSH key in the appropriate protected deployment environments. The public profile
pins host, port, host public key and an identity reference. Provision private
registry pull access with a read-only credential, separate from CI publication.

Copy `infra/host/host_infra/resources.py` from the reviewed revision and the
exported resource plan to a root-owned setup directory on the target. Run
`python3 resources.py --plan resources.json`. It checks the mounted persistent
disk, resource labels, isolation and numeric readers before creating absent
resources. It never adopts conflicting resources or starts workloads. Existing
storage labels retain their identity across the package rename.

The owner declarations create PostgreSQL storage for UID 999, Boxie and optional
Playground storage for UID 10001, and selected monitoring storage for Vector
65532, VictoriaLogs/Prometheus 65534 and Grafana 472. The approved-profile source
is root-owned, group 10001 and mode 0750, separate from Boxie's retained versions.
The host discovers the journal GID in `/etc/boxie-platform/journal-group-gid`.
Private credentials must be readable by their actual container UID; source parent
permissions and mount targets must be checked on the target host.

## Install owners in order

1. Prepare the host, protected disk, external networks and owner-declared volumes.
2. Start [Boxie's PostgreSQL](../../../tools/boxie/infra/postgres/README.md) using
   its base and production Compose files. An empty volume initializes distinct
   Boxie, Temporal persistence and visibility roles/databases.
3. Run explicit Boxie Alembic and upstream Temporal schema setup/update jobs.
   These are separately approved forward operations. Application startup and
   rollback never apply or reverse schemas.
4. Start [Temporal](../../../tools/boxie/infra/temporal/README.md), run explicit
   namespace bootstrap and verify mTLS readiness. If selected, start native
   [monitoring](../../../tools/boxie/infra/observability/README.md) and protected
   [operator ingress](../../../tools/boxie/infra/ingress/README.md).
5. Prepare Boxie's two-process configuration, stable approved-profile directory,
   dedicated mTLS identities and versioned private credential paths. Use the
   generic release tool, then run the generic create/terminal/stop smoke.
6. Optionally release [Playground](../../../tools/cli-playground/infra/README.md)
   with its own image, storage, credentials and private Boxie endpoint. Verify
   publication and terminal behavior through the product.

Native `envsubst` handles the three Prometheus identity variables and the six
operator tunnel variables. `observabilitySettings` exports the three identity
values; Access outputs provide the tunnel ID and distinct audiences. Validate
nonempty distinct audiences before substitution. There is no Python configuration
renderer. Monitoring's native rules and exporter explicitly cover `/` and
`/srv/boxie`. Grafana and Temporal UI remain private behind Access.

For monitored Boxie releases set public `BOXIE_MONITORING=true` and
`OBS_METRICS_NETWORK` in `COMPONENT_SETTINGS`; the release adapter passes the
committed monitoring overlay to native Compose. Base releases omit this selection.
Temporal's native monitoring overlay is selected by the same public flag in its
owner operation. Supporting resources remain independently owned.

## Release and rotate independently

Each application environment stores public `COMPONENT_SETTINGS` JSON separately
from private `COMPONENT_ENVIRONMENT` JSON. The descriptor declares permitted
private names. The image job publishes only image references; the protected job
uses `deploy_tool manifest` to derive committed configuration hashes and public
identity, then prepares private inputs and activates that component. Keep automatic
application deployment opt-in. Application workflows cannot provision, migrate,
update approvals or reconcile another component.

Public settings contain nonsecret versioned credential references and immutable
mounted paths. Rotation requires a new path/reference and targeted recreation;
retain old credential/configuration versions for rollback. Never mutate a mounted
credential file in place and present it as an immutable release. Prepared bundles
are private and must not be uploaded; only the public release manifest is saved.

The manual `boxie-supporting-release.yml` workflow selects exactly `postgres`,
`temporal`, `observability` or `ingress`. It excludes schema/bootstrap/storage-init
jobs, records native pins/configuration hashes privately on the host, and checks
owner readiness. Database/server upgrades have explicit operator compatibility and
recovery procedures; the workflow makes no automatic schema rollback promise.
The separate `boxie-profiles-release.yml` invokes the acknowledged atomic profile
operation. It holds the Boxie activation lock, verifies owner/image/installation,
and changes no container identity.

Before either manual workflow, install its reviewed operator checkout at
`/opt/clis/operator-checkouts/SHA`, root-owned and unchanged, including its Git
metadata and `.venv`. Install uv 0.13.0/Python 3.15, run
`uv sync --locked --all-packages` there, and retain that checkout for the operation.
Host bootstrap's Python 3.12 is sufficient for generic app releases; these manual
owner tools explicitly require the application SDK environment. They never fetch
code using the deployment SSH credential.

The protected `boxie-operations` environment supplies `DEPLOYMENT_PROFILE`,
`DEPLOYMENT_SSH_KEY` and private `OWNER_ENVIRONMENT` JSON. For Temporal include
`TEMPORAL_PROBE_ADDRESS` reachable from the host and `TEMPORAL_OPERATOR_CERTS_DIR`.
The protected `boxie-profiles` environment supplies the same SSH connection and
public `PROFILE_OPERATION` JSON: `directory`, `installation-id`, `host-id`, exact
`container`, immutable `image`, `profile`, and approved `command` array. The chosen
profile file is read from the reviewed commit. Both workflows serialize owner
operations and require reviewed source before requesting protected credentials.

## Recover and finish activation checks

Handled cancellation cleans up owned command groups before releasing the app lock.
Inspect uncertain `activating`, `recovery-required` and failed-rollback states
before retrying; the next ordinary activation fails closed. SIGKILL, lost hosts,
SSH disconnects and Docker daemon side effects require actual-state inspection.
A Boxie release ends terminals, preserves volumes and cleanup accounting, and
promises no PTY restoration. See the [release tool](../../../tools/deploy/README.md).

Before production readiness, record provider preview, first boot/reboot,
missing-disk refusal, private-network/UID access, authenticated dependency health,
DNS/ACME/Access and bypass denial, native gVisor/browser behavior, and eight-slot
combined-stack capacity on the selected VM. Local mocks and synthetic release
smoke do not establish these facts. Backup/restore implementation and its restore
proof remain activation work tracked by [issue #38](https://github.com/di-rs/clis/issues/38).
Never use `down --volumes` on production data.
