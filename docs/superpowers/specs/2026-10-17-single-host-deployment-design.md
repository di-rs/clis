# Single-host Compose deployment

Run the existing Compose projects on one Hetzner server. Automate ordinary setup
and update commands in GitHub Actions. Downtime during updates is acceptable.

This replaces the earlier deployment protocol drafted for
[PR #51](https://github.com/di-rs/clis/pull/51).

## Setup

The operator supplies external credentials and domains once. The manual setup
workflow builds the required images, runs Pulumi to create infrastructure, copies
Compose files and settings over SSH, and starts the projects. Cloud-init installs
Docker/runsc and prepares storage. Existing scripts generate internal credentials
once and initialize PostgreSQL and Temporal.

Pulumi owns cloud resources. It does not copy application files or install apps.
Keep mutable deployment scripts out of cloud-init. Retain existing private
networks, volume protection and SSH host verification.

Use fixed application directories under /opt/clis, private settings under
/etc/clis and one current image environment file per application. No release
directory tree, current-release symlink, pending marker or stage ledger is needed.

## Updates

Keep straightforward build/push and SSH steps in the existing workflows. Native
path filters select applications. A shared lockfile or root build-setting change
may redeploy both apps; occasional extra updates are an accepted simplicity trade-off.

Serialize complete setup/release workflows with clis-production, queue: max and
cancel-in-progress: false. At the start of each queued run, check out current master,
capture that checkout's SHA, and build/deploy those exact images and files.
This deploys current master instead of replaying old trigger commits.

Playground pulls and runs compose up -d --no-deps --wait gateway.
Boxie pulls first, stops its API and orchestrator, runs the existing migration
container, starts those two services and checks authenticated readiness.
PostgreSQL, Temporal and the public proxy stay running.

Use Boxie's existing shutdown and restart behavior. Pending requests may resume;
deployments do not require an empty capacity table. Remove that extra check.
Do not add a drain command or redesign the application lifecycle.

Sandbox-image updates change only the approved profile digest and recreate the
orchestrator so its file mount sees the update. Keep the current Boxie image and
other profile settings. Gateway-only changes leave Boxie and the sandbox alone.

## Failures and maintenance

An unsuccessful SSH or Compose command fails the Actions run. Inspect compose
ps/logs and rerun after correcting the problem. An interrupted operation requires
inspection before retry. Do not automatically restart old code after a failed
migration. No custom recovery protocol is added.

Retain immutable image references, private credentials and ordinary health checks.
Include the existing metrics overlay and recreate cloudflared after setup changes
its configuration. Preserve preview publishing.

Existing production activation prerequisites remain outside image builds.
Certificate expiry and backups are real operational responsibilities, but renewal
automation, backup infrastructure and restore rehearsals are separate work.

## Done

Verify fresh setup, a rerun preserving data/credentials, independent app updates,
migration failure, metrics, tunnel configuration and existing previews.
The workflows should read like ordinary Compose deployment commands.
