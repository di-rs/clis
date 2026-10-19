# Upgrade an existing installation to the component names

Use a maintenance window to move an existing Sanctuary installation to Boxie
Supervisor and cli-playground Backend. Fresh installations use the new names
without conversion. Existing installations need the state conversion below
before the renamed services start. Keep traffic and preview publication stopped
until the new services pass acceptance.

These steps target the root-operated, single-host Linux installation. Run host
commands as root with a private `umask 077`, from a checkout of the reviewed
revision. Substitute your actual paths if the installation differs. The
[design](superpowers/specs/2026-10-19-component-naming-design.md) lists the complete
vocabulary. Legacy names on this page identify migration inputs.

## Prepare the account and rollback set

Record the deployed commit, all image digests, Compose project names, external
network and volume names, installation UUID, host ID and current Alembic revision.
Export the Pulumi stack into private backup storage. Preserve the existing
Temporal namespace, workflow IDs and queues; they do not change.

Before merging, inventory the following settings in GitHub's repository and
environment settings. Copy protection rules, reviewers, deployment branch rules,
wait timers and bypass restrictions as well as variables and secrets. New
environment names do not inherit them. Re-enter secret values from the secret
manager; GitHub cannot return their plaintext values.

| Existing surface | Destination and required settings |
| --- | --- |
| Environment `platform` | `sanctuary`: variables `PULUMI_BACKEND_URL`, `SINGLE_HOST_CONFIG`; secrets `PULUMI_CONFIG_PASSPHRASE`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `HCLOUD_TOKEN`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_DNS_API_TOKEN` |
| Environment `playground` | `cli-playground`: variable `DEPLOY_HOST`; secrets `DEPLOY_SSH_KEY`, `DEPLOY_KNOWN_HOSTS`; keep `DEPLOYMENT_ACCEPTANCE_PASSED=false` until acceptance |
| Environment `playground-preview` | `cli-playground-preview`: `PLAYGROUND_URL` becomes `CLI_PLAYGROUND_URL`; `PLAYGROUND_DEPLOY_TOKEN` becomes `CLI_PLAYGROUND_DEPLOY_TOKEN`, with the same token bytes |
| Environment `boxie` | Keep the name, host and SSH credentials; keep its deployment acceptance gate false during cutover |
| Repository variables and secrets | Apply the URL/token rename wherever defined; `PLAYGROUND_INFRA_DRIFT_CHECK` becomes `SANCTUARY_INFRA_DRIFT_CHECK`, initially false |
| Registry images | Grant the same repository Actions access and deployment pull access to `sanctuary-cli-playground-backend` and `sanctuary-cli-playground-sandbox`; preserve the old packages/digests for rollback |
| Workflow files | `cli-playground-setup.yml` becomes `sanctuary-setup.yml`; `cli-playground-infra-check.yml` becomes `sanctuary-infra-check.yml`; `build-runtime-image.yml` becomes `build-image.yml` |
| Required status checks and external callers | Replace old workflow/job names in rulesets, branch protection, dispatch clients and dashboards; compare with the new workflow run's actual check names before enforcing |

The PR-build workflow's display name becomes `🏗️ cli-playground PR binary builds`;
its `workflow_run` subscriber changes with it. Its trusted source path remains
`.github/workflows/cli-playground.yml`. Release job IDs `gateway` and
`deploy-gateway` become `backend` and `deploy-backend`. Check any OIDC subject or
trusted-publisher policy that includes an environment/workflow identity. Keep
preview publication disabled throughout host maintenance, including queued runs.

Build and record approved immutable Boxie, Backend and Sandbox images from the
reviewed revision before the window. The reusable image builder is
[build-image.yml](../.github/workflows/build-image.yml); it accepts `boxie`,
`cli-playground-backend`, `cli-playground-sandbox` and `collector`. Do not dispatch
a release workflow to perform this migration: release workflows also start
services. The image-builder workflow restricts publishing to `master`.

## Drain, stop and back up

1. Stop preview publication and close public ingress. With the old Compose files
   and environment files, stop the application's `proxy` service. This disconnects
   terminals; leave Backend, API and the old Orchestrator running to finish cleanup.
   Keep any other Boxie consumers from admitting new work.
2. Wait for every sandbox to become terminal and physically removed. Confirm no
   capacity slot is occupied, no sandbox artifact reference remains, no outbox
   command is undelivered and no artifact collection is pending. The new SQL
   migration checks these conditions again and aborts before renaming anything.
3. Allow Backend recovery to finish pending creates, uploads and retirements.
   Saved bindings can still contain an old `ready` observation after physical
   cleanup; the SQL receipts below refresh these without deleting previews. The
   application converter rejects any remaining durable operation.
4. Stop the old `gateway`, Boxie `api` and `orchestrator` services using their old
   Compose definitions. Confirm they are stopped, including restart/paused states.
   Check Docker for remaining containers with this installation's
   `boxie.installation` and `boxie.host` labels. Any remaining container blocks
   cutover. Do not run old and new writers together.
5. Back up `/etc/sanctuary`, `/opt/sanctuary`, the full Boxie data directory,
   application and certificate volumes, and PostgreSQL. Back up the `boxie`,
   Temporal and visibility databases as a matched set while all application
   writers are stopped. Retain database roles, grants and the old images too.
   Follow the existing [backup/restore acceptance procedure](../packages/boxie/infra/README.md)
   and verify that the backup can be restored before continuing.

Keep PostgreSQL and Temporal available for the migration. Do not delete Docker
volumes or run `compose down -v`. Retain the original configuration tree, journal
and application volume until acceptance and the rollback retention period end.

## Stage and validate converted files

The converters operate offline. Stopping every writer is an operator precondition;
they cannot lock out an already running service. Run them against the backed-up,
quiescent state before changing the live SQL schema.

Create a private working directory, for example `/root/sanctuary-name-cutover`.
The commands below use `checkout` for the reviewed checkout, `cutover` for that
working directory and `backend_image`/`boxie_image` for approved digest references.
Set these shell variables explicitly. Set `boxie_data` to the installed
`BOXIE_DATA_DIR` (normally `/srv/sanctuary/boxie`).

```sh
python3 "$checkout/infra/environments/single-host/migrate-names.py" \
  /etc/sanctuary /etc/sanctuary.naming-new
```

The [configuration converter](../infra/environments/single-host/migrate-names.py)
copies settings, credentials and ownership into a new directory. It translates
owned keys and paths, preserves opaque secret bytes and custom profile settings,
and refuses conflicting names or nonprivate secret files. It requires an unused
destination; after a failed attempt, inspect any staged copy and choose another
unused staging path. It never modifies the original tree.

Update the staged image files to the approved digests. The converter deliberately
preserves existing image values; these are rollback values until replaced. Set
`BOXIE_IMAGE`, `CLI_PLAYGROUND_BACKEND_IMAGE` and `CLI_PLAYGROUND_SANDBOX_IMAGE` in
their corresponding `*-image.env` files, and update the current `cli-playground`
profile's `image_digest`. Its approved default command is now
`/opt/cli-playground/shell.sh`. Preserve any custom profile's intended behavior.
Keep configuration files private and do not print their contents into logs.

Export authoritative cleanup receipts from the old Boxie database after writers
have stopped. This read-only query refuses nonterminal or uncleared SQL state:

```sh
docker compose --env-file /etc/sanctuary/boxie.env \
  -f /opt/sanctuary/boxie/postgres/compose.yml exec -T postgres \
  psql -XqAt -U postgres -d boxie -v ON_ERROR_STOP=1 \
  < "$checkout/infra/environments/single-host/cleanup-receipts.sql" \
  > "$cutover/cleanup-receipts.json"
```

Require a successful exit before using the output. The application converter
matches saved sandbox IDs and accepted lifetimes against these receipts;
it refuses duplicate or conflicting entries. Already-terminal, clean bindings
remain valid when their SQL rows have aged out after seven days. A stale or pending
binding without a receipt blocks conversion: recover its cleanup evidence from
the verified backup or resolve it against the correct old installation before
retrying. Do not infer physical cleanup from an absent row alone. Keep the receipts with the
backup set and do not edit them by hand.

Convert the execution journal to a separate file:

```sh
docker run --rm --network none --entrypoint python \
  -v "$boxie_data:/state" "$boxie_image" \
  -m boxie.persistence.rename_state /state/execution.json /state/execution.converted.json
```

This command validates terminal state, physical cleanup and installation identity.
It preserves the original journal, refuses a conflicting destination and accepts
an identical retry. Current request specs and SQL request hashes move to the new
profile/command names. Completed executions retain their entire immutable
snapshot, including its old profile ID, command and configuration digest. Keep
all content-addressed profile version files unchanged: their bytes must still
match their filenames and validate those historical snapshots.

Copy the application and certificate volumes using the
[verified volume copier](../infra/environments/single-host/copy-volume.sh):

```sh
bash "$checkout/infra/environments/single-host/copy-volume.sh" \
  playground-data cli-playground-data
bash "$checkout/infra/environments/single-host/copy-volume.sh" \
  playground-certificates cli-playground-certificates
old_data=$(docker volume inspect --format '{{.Mountpoint}}' playground-data)
new_data=$(docker volume inspect --format '{{.Mountpoint}}' cli-playground-data)
docker run --rm --network none --entrypoint python \
  -v "$old_data:/old:ro" -v "$cutover:/cutover" "$backend_image" \
  -m cli_playground.rename_state /old /cutover/application \
  --receipts /cutover/cleanup-receipts.json
rsync --archive --hard-links --acls --xattrs --numeric-ids "$cutover/application/" "$new_data/"
rsync --archive --hard-links --acls --xattrs --dry-run --checksum --numeric-ids --delete --itemize-changes "$cutover/application/" "$new_data/"
```

The final dry run must report no changes. The volume copier requires root on the
Docker daemon host and `rsync`; it uses the local daemon socket and rejects running consumers,
nonlocal volume drivers and an existing destination. On failure, inspect and
remove only the failed new volume before retrying. Never remove the source.

The application converter stages a complete tree outside the volume, because an
empty Docker volume is already an existing destination. It refreshes drained bindings from SQL cleanup receipts, converts historical
termination reasons and retains preview metadata, artifact IDs, revision
files and credentials. An identical completed conversion can be retried;
conflicting destinations fail. Its OS lock releases after process termination.
An interrupted copy can leave a private `.cli-playground-rename-*` directory;
remove that orphan only after confirming no converter is running.

### Issue the renamed Supervisor certificate

Retain the existing CAs and API/operator/UI certificates. Issue a new leaf with
CN and DNS SAN `boxie-supervisor` and `clientAuth` usage using the existing client
CA in the staged TLS tree. Install it as
`tls/supervisor/boxie-supervisor.pem` and `.key`, copy the existing frontend CA to
`tls/supervisor/frontend-ca.pem`, and keep the private key mode `0600` with the
Supervisor's existing numeric owner. Retain the old leaf for rollback.

For the shipped root-operated Supervisor, issue and verify the leaf as follows:

```sh
cutover_tls=/etc/sanctuary.naming-new/tls
install -d -m 0755 "$cutover_tls/supervisor"
openssl req -newkey rsa:2048 -nodes -subj /CN=boxie-supervisor \
  -keyout "$cutover_tls/supervisor/boxie-supervisor.key" -out "$cutover/supervisor.csr"
printf 'subjectAltName=DNS:boxie-supervisor\nextendedKeyUsage=clientAuth\nbasicConstraints=CA:FALSE\n' \
  > "$cutover/supervisor.ext"
openssl x509 -req -in "$cutover/supervisor.csr" \
  -CA "$cutover_tls/ca/client.pem" -CAkey "$cutover_tls/ca/client.key" \
  -CAcreateserial -days 90 -extfile "$cutover/supervisor.ext" \
  -out "$cutover_tls/supervisor/boxie-supervisor.pem"
cp "$cutover_tls/api/frontend-ca.pem" "$cutover_tls/supervisor/frontend-ca.pem"
chmod 0600 "$cutover_tls/supervisor/boxie-supervisor.key"
chmod 0644 "$cutover_tls/supervisor/boxie-supervisor.pem" "$cutover_tls/supervisor/frontend-ca.pem"
chown --reference="$cutover_tls/orchestrator/boxie-orchestrator.key" \
  "$cutover_tls/supervisor/boxie-supervisor.key"
openssl verify -CAfile "$cutover_tls/ca/client.pem" \
  "$cutover_tls/supervisor/boxie-supervisor.pem"
bash "$checkout/packages/boxie/infra/certificates.sh" "$cutover_tls"
```

Do not regenerate the CA tree. Update any custom Temporal certificate-subject allowlist at the same
maintenance boundary. The shipped configuration trusts the existing client CA;
its namespace, lifecycle workflow IDs and task queues stay unchanged. Run the
certificate validator against the staged TLS directory before activation.

## Apply and accept

1. Review Pulumi's preview using the existing stack and project. DNS resources now
   carry aliases for their old logical names. Require no resource replacement
   caused by this rename; stop if a preview proposes one. Preserve the stack and
   resource IDs, rather than creating a new stack.
2. Save the old Compose definitions, then copy the reviewed Boxie infrastructure
   into `/opt/sanctuary/boxie` and the application infrastructure into
   `/opt/sanctuary/cli-playground`. Keep `/opt/sanctuary/playground` for rollback.
   Validate the new Compose configuration using the staged environment files.
3. Run the new Boxie `migrate` service once, using
   [compose.operations.yml](../packages/boxie/infra/compose.operations.yml) and the
   approved `BOXIE_IMAGE`. Its command is `python -m boxie.persistence.migrate
   --dsn-file /run/secrets/postgres-dsn`. This applies Alembic revision
   `0005_component_names` transactionally. A failed drain/conflict check leaves
   SQL unchanged. Do not use Alembic's offline SQL output to migrate legacy
   requests: their canonical request hashes are calculated by the online upgrade.
4. With writers still stopped, retain `execution.json` under a backup name and
   install `execution.converted.json` as `execution.json`, preserving its owner
   and private mode. Replace `/etc/sanctuary` with the staged configuration tree,
   retaining the old tree. Configuration paths deliberately point at the final
   `/etc/sanctuary` location.
5. Remove the stopped old application and Orchestrator containers using their
   saved Compose definitions, without removing volumes. Inspect consumers before
   replacing the old application proxy network with `cli-playground-proxy`.
   Keep `boxie-control` and `boxie-consumers`. Start the renamed Supervisor, API,
   Backend and proxy using the normal installation/deployment scripts and the
   approved revision. Keep public traffic blocked externally during acceptance.
6. Confirm Boxie authenticated readiness, supervisor health, PostgreSQL access,
   Temporal mTLS access, namespace schedules and observability labels. Open an
   existing preview, confirm its retained artifact/revision, then create a new
   sandbox, use its terminal and verify cleanup. Run the
   [native acceptance tests](testing.md#independent-service-packaging) on Linux with
   runsc, including reconnect and Supervisor restart. Inspect logs for stale
   configuration names, missing profiles or ownership failures.
7. Restore public ingress and preview publication only after acceptance. Enable
   the deployment acceptance gates and optional drift schedule. Retain backups,
   old volumes and old images until the cutover is accepted operationally.

## Roll back as a matched set

If any conversion or validation fails, keep traffic stopped. Before SQL migration,
the original files and volumes remain usable. After SQL migration or any new
service write, stop every new writer and restore the matching pre-cutover
PostgreSQL databases, journal, configuration, application volume, certificates,
Compose definitions and old images together. Restore the old GitHub environment
references and disable renamed release/publishing workflows before reopening
traffic. Do not run old code against the renamed SQL schema or partially migrated
files. Alembic downgrade intentionally refuses this change; rollback uses the
verified backup set.
