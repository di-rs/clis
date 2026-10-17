# Single-host deployment design

Status: proposed implementation contract, ready for review. This document records
the requirements gathered for a fresh implementation from `origin/master` at
`3bde1d85ca0469ad1b6c471ac57881c03451df24`. It supersedes PR #42 as the basis for
this work. No code, tests or plans from that PR are to be cherry-picked.

## Outcome

An operator supplies account credentials and installation settings once, then
runs a GitHub Actions setup workflow to provision one Hetzner VM and install
Boxie, Playground and their supporting services. Later merges automatically
deploy affected applications. Rust CLI PRs continue publishing their affected
binaries to the existing Playground preview flow.

Keep the system understandable through native Pulumi, cloud-init, SSH and Docker
Compose operations. Ansible and a custom deployment framework are unnecessary
for this fixed installation. Application downtime is acceptable. Failed updates
stop and report failure; recovery is an explicit operator action.

## Agreed requirements

- Use one fresh Hetzner VM, Cloudflare DNS and Access, Pulumi, and R2-backed state.
  No existing deployment or data needs migration.
- Include existing monitoring, Grafana and Temporal UI in the installation.
  Protect operator dashboards with Cloudflare Access; keep Boxie private.
- Provision and install through a manual Actions workflow. Once activation gates
  pass, automatically deploy affected applications on merges to `master`.
- Run Boxie's Alembic migrations automatically during Boxie deployment. Keep
  PostgreSQL and Temporal server/schema upgrades as separate maintenance work.
- Allow updates to end active sessions. Do not implement draining, seamless
  handover, automatic rollback or schema downgrade.
- Preserve automatic affected-CLI selection, stable PR URLs, current-head
  publication checks, close/reopen handling and existing-session behavior.
- Generate service credentials during installation and retain them on reruns.
  The operator supplies external account credentials, not internal passwords.

## Ownership and tools

| Owner | Responsibility |
| --- | --- |
| `infra/host/` | VM, firewall, protected disk, SSH identity and first-boot configuration |
| `infra/environments/single-host/` | Pulumi entry point, installation settings and setup order |
| `tools/boxie/infra/` | API/orchestrator configuration, PostgreSQL, Temporal, monitoring, operator ingress, certificates and approved profiles |
| `tools/cli-playground/infra/` | Playground, public proxy, DNS routes, preview token and product sandbox image |
| GitHub Actions | Build images, select affected applications, transfer committed files and invoke fixed owner scripts |

Pulumi manages cloud resources and outputs a pinned SSH connection. Cloud-init
prepares the operating system. It is first-boot configuration, not an ongoing
configuration reconciler. Explicit host maintenance handles later host changes.
Compose remains the authoritative container definition. Retain distinct existing
project names, private networks and storage ownership.

Move only infrastructure that actually belongs to the shared host. PostgreSQL,
Temporal, observability and ingress remain under Boxie. Keep the current Python
workspace, two Boxie application processes, SQLModel/Alembic and embedded Temporal
workers. Preserve current sandbox limits, eight slots and lifecycle policies.

## Operator inputs and first installation

The operator first configures GitHub environments and these external inputs:

- Hetzner project API token, region, approved VM size and disk size.
- Cloudflare account/zone IDs, scoped provisioning token, separate DNS renewal
  token, Playground hostname, dashboard hostnames and authorized operator emails.
- R2 backend URL, bucket credentials and Pulumi encryption passphrase. The state
  bucket must exist before provisioning; recover its passphrase outside the VM.
- ACME contact email, an administrative SSH public key and SSH source CIDRs that
  actually allow the chosen Actions runners. Never temporarily open the firewall
  from ordinary application jobs. Standard hosted runners require an explicitly
  accepted network policy; a restricted policy needs a runner with known egress.
- Registry read credentials if the published images are private.

Reuse the existing `platform` infrastructure environment protections where
configured. Use separate `boxie`, `playground` and `playground-preview` environments
for application SSH access and preview publication. Ordinary release jobs receive
neither R2 nor cloud provisioning credentials. Infrastructure approval remains
manual; application environments allow automatic trusted-mainline deployment.

Bootstrap installs Docker, Compose and runsc, mounts the persistent disk, creates
private directories and configures journald. Docker must not start workloads when
the persistent mount is absent. Verify the actual Linux numeric file readers in
current images; do not inherit UID assumptions from PR #42.

Installation then creates external networks and storage, generates internal
credentials and Temporal certificate identities, starts PostgreSQL, initializes
Temporal schemas and namespace, and starts Temporal. It runs Boxie migrations,
starts Boxie and monitoring/operator ingress, then starts Playground and its proxy.
Verify service health and a real create/terminal/stop lifecycle before success.

Secrets are written privately on the host, never printed or uploaded as Actions
artifacts. Use existing provider-generated SSH host identity for initial trust,
not unauthenticated `ssh-keyscan`. Supply client trust through SSH known-hosts.
Keep service certificates and CA keys recoverable separately from runtime access.

There is one explicit handoff after provisioning: the operator uses a local
helper to install the deployment key/host identity in application environments
and the host-generated preview publication token in `playground-preview`.
The helper uses the operator's existing GitHub authentication, not a repository
administration token stored in deployment workflows. It must not print secrets.
Setup can install and verify the services before this handoff; automatic updates
and preview publication are enabled only afterward. Repeating setup does not
regenerate credentials or reset databases. Partial credentials fail with a repair
instruction rather than silently creating inconsistent replacements.

## Application deployment contract

Use fixed owner scripts and a shared, small transport script. Target approximately
150–300 lines for application deployment scripts; this is an estimate and review
budget, not a reason to omit failure handling. Bootstrap, workflows and tests are
additional. Reconsider the approach before adding a generic Python release engine,
descriptor schema, custom archive parser or custom process supervisor.

Actions selects committed source by full SHA, builds images and records registry
digests. Only trusted mainline code receives deployment credentials. Transfer
owner files from that commit into `/opt/clis/releases/<application>/<run-id>/`;
keep credentials under `/etc/clis/` and data on the protected mount. A release
contains plain Compose files, nonsecret image assignments and its source SHA.
Do not build from dirty files or reinterpret user input as shell code.

Use a root-owned transient systemd service for the remote operation with a
15-minute runtime limit and control-group cleanup. A host-wide `flock` serializes
mutating installation/deployment/maintenance operations. SSH disconnect does not
abandon a background shell: the systemd unit completes or times out while holding
the lock. A reconnect inspects its status. Never report success merely because the
unit was started. Actions concurrency queues deployments without cancelling them.
Record interrupted or timed-out operations as requiring inspection; Docker daemon
effects may outlive a killed client, so the next job must not blindly continue.

For both applications, validate Compose and pull images before interrupting the
running version. Use `up --no-deps --wait` on an explicit service list. A Playground
runtime update targets its gateway and must preserve the proxy and all Boxie
supporting services. Proxy changes use an explicit owner maintenance operation.

For Boxie, stop admission and end sessions through existing lifecycle/shutdown
behavior; verify cleanup/accounting rather than assuming process exit removes
sandboxes. Stop both API and orchestrator, run
`python -m boxie.persistence.migrate --dsn-file ...` in the target image as a
one-shot Compose service, then start both and check dependency readiness. On
migration failure leave application processes stopped and preserve diagnostics.
First installation uses this same application migration path.

Write the last successful revision and image references only after readiness.
On failure retain the attempted revision and failed stage, with no automatic
rollback. Manual recovery selects an existing successful image and its matching
configuration, rather than rebuilding an old SHA. Boxie recovery must check schema
compatibility; never downgrade the database. Document recovery from each stage.

## Selecting updates and preserving previews

Application changes include runtime sources, Dockerfiles, applicable configuration
and dependency changes. Compare relevant locked dependency closures to avoid
deploying Boxie for a Playground-only dependency change. Supporting-service files
must not trigger their automatic upgrade. Workflow tests pin this selection.
Queued application jobs check newer successful deployments and cannot downgrade
an application automatically; manual recovery is explicitly distinguished.

A product sandbox-image change also changes Boxie's operator-approved profile.
Treat it as a coordinated update: publish the sandbox image, update only the
approved `playground` profile image digest through trusted setup/deployment code,
restart Boxie using its existing image/configuration, then deploy Playground if
needed. Preserve other profile fields and entries. Existing sessions may end.
This is a proposed implementation choice for review, not an independent profile
management subsystem. Plain Rust CLI PR artifacts never change this approval.

Preserve the current PR build/publish implementation on master. Add regression
coverage and connect its token/URL; do not import PR #42's preview redesign or ZIP
parser changes. Artifact publication updates preview data, not running services.
Untrusted PR builds receive neither SSH nor deployment credentials.

## Verification and activation

Use existing lifecycle fixtures with small production-script adapters. Prove a
fresh install, harmless setup rerun, immutable independent update, migration
success/failure, manual recovery and PR artifact publication. Include real Linux
runsc isolation, file permissions, missing-disk/reboot behavior and interruption
of a real SSH connection. A synthetic HTTP fixture is not full acceptance.

Provider preview, real DNS/ACME/Access and selected-VM capacity require an authorized
disposable environment. Report prerequisites honestly, without silently provisioning
paid resources. Local configuration checks cannot establish these properties.

Current master also requires off-host backup and restore evidence before production
activation; see [runtime prerequisites](../../../tools/boxie/infra/runtime.md).
Preserve that gate. Backup implementation is a separate prerequisite, not implicitly
added to this plan. Until it is satisfied or explicitly revised by the user, the
result can be accepted on a disposable host but production auto-deployment stays
disabled. Data on one protected disk is not an off-host backup.

## Scope boundaries

No Ansible, Kubernetes, generic deployment CLI, automatic rollback, schema downgrade,
high availability, zero-downtime updates, multi-host scheduling or general profile
management. No unrelated runtime refactoring, preview controller rewrite or new
observability framework. Existing supporting services are installed and remain
independently operable; future upstream upgrades follow their owner procedures.

## Upstream references

- [Pulumi Command](https://www.pulumi.com/registry/packages/command/) explains explicit command lifecycle triggers.
- [Cloud-init user scripts](https://docs.cloud-init.io/en/latest/explanation/format/user-data-script.html) explains first-boot execution.
- [Compose in production](https://docs.docker.com/compose/how-tos/production/) describes single-server deployment.
- [GitHub deployment controls](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/control-deployments) describes environment restrictions and secrets.
