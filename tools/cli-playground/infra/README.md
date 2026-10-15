# Playground hosting

Set up the playground entirely in the Cloudflare, Hetzner and GitHub websites.
GitHub Actions provisions the host and deploys the application; no local Pulumi,
Docker, SSH or deployment commands are required for setup or routine releases.
The private Cloudflare R2 bucket holds Pulumi state independently of the VPS.

Production runs one Ubuntu 24.04 CX33 in Nuremberg, with a retained IPv4 address,
Docker, gVisor and Traefik. The control hostname, such as `play.thesecret.dev`,
serves the gateway; PR previews use `pr-N.play.thesecret.dev/p/<identifier>`.
Control-host `/p/<identifier>` links are also supported. Previews expire after
seven days or PR closure. There is no permanent preview containing all `master` binaries.

## 1. Prepare the accounts and domain

You need access to the domain's registrar, a Cloudflare account with R2 enabled,
a Hetzner Cloud project, and administrator access to the GitHub repository.
Merge these workflows into the repository's `master` default branch before
running them. Keep `PLAYGROUND_AUTO_DEPLOY` unset or `false` until step 7 succeeds.

1. In Cloudflare, select your account, open **Domains**, and select the existing
   domain, such as `thesecret.dev`. If it is new to Cloudflare, choose **Onboard a
   domain**, enter the apex domain, choose a plan and review imported DNS records.
2. For a domain registered elsewhere, follow Cloudflare's instructions to set
   its assigned nameservers in the registrar's website. Preserve existing website
   and email records. Wait until Cloudflare shows the zone as **Active** before
   deploying. See [Cloudflare domain onboarding](https://developers.cloudflare.com/fundamentals/manage-domains/add-site/).
3. On the domain's **Overview** page, find the **API** section and copy its
   **Zone ID** and **Account ID**. They are different values. The zone ID selects
   the DNS zone; the account ID belongs in the R2 endpoint. See
   [finding these IDs](https://developers.cloudflare.com/fundamentals/account/find-account-and-zone-ids/).
4. Choose an unused hostname inside that zone, such as `play.thesecret.dev`.
   Pulumi will create its **A** record and `*.play.thesecret.dev`, pointing to the host's IPv4, with
   **DNS only** proxy status and a 300-second TTL. Do not create it by hand.
   Resolve any existing A, AAAA or CNAME record for that hostname before setup;
   an existing service requires an explicit migration or import, not deletion
   without checking its owner.

This deployment uses a control A record and a wildcard A record, with no AAAA
record. Traefik obtains and renews the control/wildcard certificate using ACME
**DNS-01** through Cloudflare. Keep ports 80 and 443 reachable and both records
DNS-only. Infrastructure setup uses its own token; a separate DNS-renewal token
is installed privately on the VPS and exposed only to the Traefik container.

## 2. Create the private R2 state bucket once

1. Open **R2 object storage → Overview** in the same Cloudflare account. Complete
   R2 activation and billing setup if prompted.
2. Choose **Create bucket** and a dedicated name, for example
   `clis-playground-state`.
3. Under **Location**, choose **Specify jurisdiction → European Union (EU)**.
   EU jurisdiction is recommended for this deployment. It is different from a
   Western Europe location hint and cannot be changed after bucket creation.
   See [R2 data location](https://developers.cloudflare.com/r2/reference/data-location/).
4. Select **Standard** as the default storage class and create the bucket. This
   state is small and updated regularly; see [R2 storage classes](https://developers.cloudflare.com/r2/buckets/storage-classes/).
5. Keep the bucket private: leave the public development URL disabled and do not
   attach a public custom domain. Buckets are private by default; see
   [creating R2 buckets](https://developers.cloudflare.com/r2/buckets/create-buckets/).
   Do not add expiration rules or retention locks covering the active `.pulumi`
   prefix. Pulumi must update checkpoints and remove its concurrency locks.

There is no bucket-creation workflow or R2 bootstrap token. This bucket remains
in Cloudflare when the playground server changes. Keep its account, name and
jurisdiction stable.

## 3. Create the credentials

Store each value in your password manager while creating it. Do not put secrets
in repository files, Actions variables, issues or workflow inputs.

### R2 credentials

From **R2 object storage → Overview**, find **Account Details → API Tokens →
Manage**. Create an account API token if your account permissions allow it, or a
user API token otherwise. Select **Object Read & Write**, then **Apply to specific
buckets only**, and select the state bucket. Save the generated **Access Key ID**
and **Secret Access Key**; the secret is only shown once. Use these S3 credentials,
not the API-token string, in GitHub. See [R2 authentication](https://developers.cloudflare.com/r2/api/tokens/)
and the [S3 setup guide](https://developers.cloudflare.com/r2/get-started/s3/).

The EU bucket's S3 endpoint is:

```text
https://ACCOUNT_ID.eu.r2.cloudflarestorage.com
```

Replace `ACCOUNT_ID` with the copied Cloudflare account ID. The default endpoint
without `.eu` cannot access this EU bucket.

### Cloudflare DNS tokens

Open **My Profile → API Tokens → Create Token** and use **Edit zone DNS** or a
custom token. Name it for playground setup. Set permissions to **Zone / DNS /
Edit** and **Zone / Zone / Read**, with **Zone Resources → Include → Specific
zone → thesecret.dev** (or your chosen zone). Review and create the token, then
save its value. Do not add account-wide or R2 permissions. See
[Cloudflare token creation](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/).

Create a second token with the same zone-scoped permissions, named for playground
certificate renewal. Save this as `CLOUDFLARE_DNS_API_TOKEN`; the setup token stays
`CLOUDFLARE_API_TOKEN`. Separate tokens allow independent rotation and revocation,
but both can edit DNS within the selected zone. They are not scoped to individual
challenge records. The renewal token is used as Traefik's `CF_DNS_API_TOKEN`;
see the [Cloudflare DNS challenge provider](https://go-acme.github.io/lego/dns/cloudflare/).

### Hetzner token and application secrets

In the Hetzner Console, create or select a dedicated Cloud project. Open
**Security → API tokens → Generate API token**, select **Read & Write**, and save
the token. Pulumi creates the server, IP, firewall and SSH keys; no manual server
creation or SSH-key upload is needed. See [Hetzner token creation](https://docs.hetzner.com/cloud/api/getting-started/generating-api-token/).

Generate two independent values with your password manager:

- A strong random `PULUMI_CONFIG_PASSPHRASE`, used to encrypt secrets in state.
  Keep a recovery copy outside GitHub. Changing the GitHub value alone does not
  re-encrypt existing state and will prevent it from being decrypted.
- A strong random `PLAYGROUND_DEPLOY_TOKEN`, used by the PR publisher and gateway.
  A 64-character random hexadecimal value fits the application's literal value
  validation; do not use spaces, quotes, `$`, backslashes or `#`.

## 4. Configure GitHub variables and secrets

First create the protected environment, then add its secrets. In repository
**Settings → Environments → New environment**, enter **playground**. Under
**Deployment branches and tags**, choose **Selected branches and tags**, add a
rule with **Ref type: Branch** and **Name pattern: master**, and save it. Do not
add a tag rule. This prevents other branches from accessing the environment
credentials. Add required reviewers if your deployment policy calls for them;
they apply to both setup and runtime deployment jobs. See
[GitHub environment configuration](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments).

In **Settings → Secrets and variables → Actions → Variables**, use **New
repository variable** for each entry below. Keep these at repository scope;
the release job checks the opt-in variable without using the environment. See
[GitHub configuration variables](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-variables).

| Repository variable | Value |
| --- | --- |
| `PULUMI_BACKEND_URL` | The full backend URL below, with your bucket and account ID |
| `CLOUDFLARE_ZONE_ID` | The domain's zone ID: 32 lowercase hexadecimal characters |
| `PLAYGROUND_URL` | `https://play.thesecret.dev`, or your chosen HTTPS origin, without a path, port or trailing slash |
| `ACME_EMAIL` | Your certificate contact email |
| `PLAYGROUND_AUTO_DEPLOY` | `false` initially; set to exactly `true` after successful setup |

For the recommended EU bucket, paste this as one line in `PULUMI_BACKEND_URL`:

```text
s3://clis-playground-state?endpoint=ACCOUNT_ID.eu.r2.cloudflarestorage.com&region=auto&awssdk=v2&s3ForcePathStyle=true
```

Replace `clis-playground-state` and `ACCOUNT_ID` with your actual values. Do not
include `https://` inside the `endpoint` parameter, and do not include credentials
in this URL. This is Pulumi's S3-compatible backend URL, not the public R2 URL.
If you deliberately chose another jurisdiction, use its matching endpoint from
Cloudflare. See [Pulumi S3-compatible backends](https://www.pulumi.com/docs/iac/concepts/state-and-backends/).

In **Settings → Environments → playground → Environment secrets**, use **Add
secret** for these six entries:

| Environment secret | Value and use |
| --- | --- |
| `HCLOUD_TOKEN` | Hetzner project Read & Write token; setup only |
| `CLOUDFLARE_API_TOKEN` | Zone-scoped DNS token; setup only |
| `CLOUDFLARE_DNS_API_TOKEN` | Separate zone-scoped DNS token; runtime certificate renewal |
| `R2_ACCESS_KEY_ID` | R2 Access Key ID; state access |
| `R2_SECRET_ACCESS_KEY` | R2 Secret Access Key; state access |
| `PULUMI_CONFIG_PASSPHRASE` | Passphrase used to encrypt/decrypt state secrets |

Finally, open **Settings → Secrets and variables → Actions → Secrets → New
repository secret**, and add `PLAYGROUND_DEPLOY_TOKEN`. It must be a repository
secret because the trusted PR preview workflow also needs it. Do not add
an environment secret with the same name: an environment override could give
the gateway a different token from the publisher. See [GitHub Actions secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets).

The workflows map `R2_ACCESS_KEY_ID` to `AWS_ACCESS_KEY_ID` and
`R2_SECRET_ACCESS_KEY` to `AWS_SECRET_ACCESS_KEY` internally. These are SDK naming
conventions for Cloudflare's S3-compatible service. No AWS account, AWS IAM user,
AWS secret or Pulumi Cloud account is needed.

## 5. Check GitHub Container Registry access

The release workflow uses GitHub's short-lived `GITHUB_TOKEN` to publish:

- `ghcr.io/<owner>/<repository>-playground-gateway`
- `ghcr.io/<owner>/<repository>-playground-sandbox`

Names are lowercase. Packages can remain private; no personal access token is
needed. The workflow requests `packages: write` for its build and `packages: read`
for deployment. Your repository/organization Actions policy must allow the
workflow's actions and package publication.

For existing packages, open each package on GitHub, choose **Package settings →
Manage Actions access → Add repository**, select this repository, and grant
**Write** so it can publish and pull. New packages published by this workflow
normally inherit repository access; organizations can disable that inheritance.
See [package Actions access](https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility).

CI sends temporary registry authentication over verified SSH. The remote
authentication file is removed with deployment staging files after the command
finishes. The VPS pulls images; it does not build them.

## 6. Review the production defaults

Open [Pulumi.production.yaml](Pulumi.production.yaml) in GitHub. The defaults are:

| Setting | Default |
| --- | --- |
| Location | `nbg1` (Nuremberg) |
| Server | `cx33`, Ubuntu 24.04, IPv4 only |
| Hetzner backups | `false` |
| Resource protection | `true`: Pulumi protection, server delete/rebuild protection and IPv4 delete protection |
| SSH source networks | `0.0.0.0/0` on TCP 22, public-key authentication only |

GitHub-hosted runners change IP addresses, so the default exposes SSH to IPv4
clients and authenticates with the generated key. Password and keyboard-interactive
SSH authentication are disabled. HTTP, HTTPS and ICMP are also allowed. There
is no temporary per-run firewall rule or firewall cleanup job.

Pulumi generates separate ED25519 deployment and host keys. Their private fields
and the derived cloud-init data are encrypted in R2 with the passphrase. The
runner creates private temporary identity files and pins the server's public
host key from state. No manual first connection or trust-on-first-use step is
needed. The host private key is supplied to Hetzner through cloud-init user-data;
the deployment private key and provider API tokens are not installed there.
Treat state access plus the passphrase, Hetzner project access, and trusted
deployment code as administrative access.

Change defaults through a reviewed PR before running setup. `sshPublicKey` can
add an administrator public key at host creation, and `sshCidrs` controls every
SSH client's allowed IPv4 networks. Narrowing it without a runner with a stable,
allowed egress address blocks GitHub-hosted deployment. Host-key and cloud-init
changes are not a routine way to update an already running host.

## 7. Run setup, verify it, then enable releases

1. In GitHub, open **Actions → CLI playground setup → Run workflow**.
2. Select **Branch: master** and choose **Run workflow**. This workflow has no
   inputs. If the button is missing, confirm the workflow is merged into the
   default branch and you have write access. See
   [running a workflow manually](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow).
3. Open the run and approve any configured environment review. The **provision**
   job validates configuration, logs into the existing R2 bucket, selects or
   creates the `production` stack, runs a refreshed Pulumi preview, and applies
   the infrastructure. The preview is logged; there is no separate approval
   pause between preview and apply.
4. After provisioning, setup calls **CLI playground release**, which builds
   gateway and sandbox images from that same workflow commit. Its summary and
   `cli-playground-release` artifact record the commit and immutable image
   digests. Its **deploy** job installs that release on the existing host.
5. Deployment reads the existing SSH identity from R2, waits for SSH and
   cloud-init, checks for gVisor, pulls images, installs configuration and starts
   Compose. It verifies public HTTPS `/healthz` on both the control hostname and
   `pr-1.<control-host>` before success, including wildcard TLS.
6. Confirm all jobs succeed, then open `https://play.thesecret.dev/healthz` using
   your chosen hostname. Expect `{"status":"ok"}`. In Cloudflare's DNS page,
   confirm both managed A records are DNS-only and point to the Hetzner IPv4.
7. Edit the repository variable `PLAYGROUND_AUTO_DEPLOY` to exactly `true`.
   Subsequent relevant pushes to `master` now build and deploy the runtime.

A push only triggers a runtime release for the paths listed in
[cli-playground-release.yml](../../../.github/workflows/cli-playground-release.yml):
application/frontend, fixtures, container and deployment files, workflow files,
and shared dependency inputs. Infrastructure-only changes such as `host.py` or
`Pulumi.production.yaml` require another manual **CLI playground setup** run.
Changing a variable or secret alone does not trigger either workflow.

To publish the first PR preview, open or update a PR that changes an eligible
Rust CLI. Its build/preview workflows create the preview and post a link. Setup
does not republish old PR artifacts or create a `master` preview. A fresh eligible
PR build is needed for an older PR without a working preview. PR links are public.

## Routine operations

| Task | Browser action |
| --- | --- |
| Deploy current runtime manually | **Actions → CLI playground release → Run workflow → master**; this works even while auto-deploy is `false` |
| Pause automatic runtime releases | Set repository variable `PLAYGROUND_AUTO_DEPLOY` to `false`; this does not stop the running service or an already queued/running job |
| Apply reviewed infrastructure changes | Merge into `master`, then run **CLI playground setup**; it also builds and deploys that commit's runtime |
| Change certificate email, renewal token or application token | Update the GitHub value, then run **CLI playground release** on `master` |
| Change hostname or DNS zone | Plan the DNS migration, update the variables, then run **CLI playground setup**; runtime deployment rejects a URL that differs from state |
| Undo application code | Revert it in a reviewed PR, merge to `master`, then let auto-deploy run or start **CLI playground release** manually |

Runtime deployment uses Pulumi only to read existing stack outputs from R2. It
does not refresh state, call `pulumi up`, change the firewall, or use the Hetzner
or Cloudflare provisioning token. It installs the separate certificate-renewal
token for the proxy. The R2 credential is still bucket-scoped Object Read
& Write because setup uses the same credential. Provisioning and deployment jobs
share a concurrency group to prevent simultaneous host changes.

A runtime update can restart the gateway and ends active terminal sessions.
Their files are disposable. Published PR metadata and binaries remain under
`/var/lib/cli-playground` across normal releases. New PR uploads advance the
preview's runnable version at the same URL. Existing sessions retain their exact
version and files until disconnect or expiry; unused versions are then removed.
The page distinguishes latest-build status from its current terminal revision,
and an explicit switch/reset starts a new workspace with the latest available
artifact. Reopening a PR reconciles its latest current-head run when available.

### Reruns and failures

After correcting a credential or setting, open the failed run and use GitHub's
rerun controls, or start a new manual run of the appropriate workflow. A rerun
uses the original commit/ref; a new **Run workflow → master** uses the current
branch revision. See [GitHub reruns](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs).
Rerunning the release build makes a new run-attempt tag and can produce new
digests; it is not a promise to reproduce the previous images byte-for-byte.
The reusable runtime-deploy workflow has no standalone **Run workflow** button.

Setup reruns use existing R2 state and keys. A partial provisioning failure can
leave billable resources, so correct the issue and reconcile the same stack.
Do not recover by changing to an empty bucket or deleting state. Check the
workflow step that failed, then use the relevant account page:

- **State access:** verify bucket name, account ID, EU endpoint, bucket-scoped
  S3 credentials and the original passphrase. Runtime deployment allows two
  minutes for the state read, including storage retries. A timeout fails the run
  and cleans up temporary files; rerun after resolving backend access.
- **Provider setup:** check the Hetzner project token, available server type/
  location, zone ID, DNS-token scope and hostname conflicts.
- **Image publication/pull:** check both packages' Actions access and repository/
  organization policies.
- **SSH/bootstrap:** check server status and TCP 22 in the Hetzner Console.
  Host-key verification failure requires investigating the host/state mismatch;
  do not disable host-key verification.
- **Public HTTPS:** check the active zone, both DNS-only A records, absence of an
  AAAA record for this IPv4-only host, ports 80/443, the renewal token's zone
  permissions and any certificate-authority restrictions. Check both control and
  PR-host health URLs; DNS propagation can outlast the health-check window.

Images are pulled before Compose changes the running release. There is no
automatic rollback after a Compose or public-health failure; the host may already
be running the new release. Correct the cause and rerun, or deploy a reviewed
revert. Rerunning an old successful workflow can deploy old code and is not the
documented rollback path. Existing release directories are not an automatic
rollback service, and neither a revert nor a rerun restores lost session files
or reverses application-data changes.

Host diagnostics are in `/var/log/cli-playground-deploy.log`, cloud-init logs and
Docker logs. These private host logs are not uploaded by the workflows; detailed
host repair may require a separately planned administrator recovery procedure.

### State, data and host recovery

Keep an independently controlled backup of the entire R2 `.pulumi` prefix and a
separate copy of the passphrase. Pulumi checkpoint history in the same bucket is
not an independent backup. R2 does not implement S3 object versioning; see its
[S3 compatibility table](https://developers.cloudflare.com/r2/api/s3/api/).
Backup and restore automation is not included. Do not apply a retention lock to
active state or locks; protect backup copies separately. Never upload decrypted
state, private keys or application environment files as GitHub artifacts.

R2 state records infrastructure and encrypted keys, not the VPS's application
data. A host replacement does not restore these directories:

| Host path | Contents |
| --- | --- |
| `/var/lib/cli-playground` | Published PR metadata and binary batches |
| `/var/lib/cli-playground-proxy` | ACME account and certificates |
| `/etc/cli-playground` | Private application configuration |
| `/opt/cli-playground/releases` | Deployed manifests and Compose files |

Back up application data and certificates separately if they must survive host
loss. Fresh publication can recreate PR previews from a new eligible build;
temporary session workspaces cannot be recovered. Hetzner backups are disabled
by default. Loss of the passphrase prevents decrypting the managed SSH identities.
Restoring state or rotating its encryption provider requires a reviewed recovery
procedure; there is no recovery button in these workflows.

Server deletion/rebuild and IPv4 deletion are protected by default. A retained IP
does not make replacement automatic: it must be detached and reassigned in a
compatible location, and Pulumi replacement ordering must be planned. The current
workflows do not implement or validate an automated replace-and-restore sequence.
Do not delete the server or set `protect: false` as a generic troubleshooting step.
Plan resource protection changes, downtime, state handling, IP reassignment and
data restoration before a replacement. Cloud-init identity changes only take
effect during the host's first boot.

The [Linux sandbox workflow](../../../.github/workflows/cli-playground-sandbox.yml)
checks real gVisor execution. Missing gVisor fails deployment. The setup and release
workflows verify real provider access and public HTTPS when an operator runs them;
documentation and local checks alone do not establish a successful live deployment.
