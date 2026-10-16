# Playground infrastructure

Production activation is unavailable during PR1–PR3. Setup provisions the protected
host and DNS only; it no longer chains a release. Release, Python deploy, CI deploy
and the remote deployment shell fail before side effects with an explicit PR4
diagnostic. There is no bypass. The retained release helpers and tests do not
establish working split-service production deployment.

## Independent local unit

First obtain a private Boxie URL and consumer credential from its operator; see
[Boxie infrastructure](../../boxie/infra/README.md). Boxie's approved `playground`
profile must use the immutable product sandbox image, command
`["/opt/playground/shell.sh"]`, environment allowlist `TERM=["xterm-256color"]`, and
artifact mount `/opt/artifact`. The declaration is in
[profiles/playground.json](profiles/playground.json); replace the placeholder digest
and merge it into the **operator-owned Boxie profiles file**. The product cannot
upload or change its profile. Build the product sandbox separately:

```sh
docker build -f tools/cli-playground/Sandbox.Dockerfile -t clis-playground-sandbox:local .
docker build -f tools/cli-playground/infra/Playground.Dockerfile -t cli-playground:local .
```

From the repository root, create an external product-data volume and set the
consumer connection. The network must already exist; it can belong to a separate
Boxie installation. There is no cross-project `depends_on`.

```sh
docker volume create playground-data
export PLAYGROUND_PROJECT_NAME=cli-playground
export PLAYGROUND_DATA_VOLUME=playground-data
export PLAYGROUND_CONSUMER_NETWORK=boxie-consumers
export PLAYGROUND_BOXIE_URL=http://boxie-api:8090
export PLAYGROUND_BOXIE_CREDENTIAL_FILE=/private/path/consumer.secret
export PLAYGROUND_DEPLOY_TOKEN=local-deploy-token
export PLAYGROUND_PUBLIC_URL=http://localhost:8000
docker compose -f tools/cli-playground/infra/compose.yml up --build -d
```

Both local and production manifests default to project `cli-playground`. Override
`PLAYGROUND_PROJECT_NAME` for another installation; keep it distinct from Boxie's
`BOXIE_PROJECT_NAME` (default `boxie`). Do not give the two units a shared
`COMPOSE_PROJECT_NAME` or `--project-name`, which overrides the manifest identity.
Their external network and volume identifiers remain independently configured.

The credential must be a regular non-symlink file, mode `0600`, containing a
16–4096-character printable token. The local unit publishes only localhost:8000.
The independently resolvable production manifest uses an explicit immutable
`PLAYGROUND_IMAGE`, external `PLAYGROUND_PROXY_NETWORK` and
`PLAYGROUND_CERTIFICATES_VOLUME`; only Traefik publishes 80/443 and receives the
Cloudflare certificate-renewal credential. Boxie's artifact data and orchestrator
credential never enter Playground. Runtime images contain only their own installed
package/dependencies and, for Playground, its real built frontend assets.

Operator modules are installed as `playground_infra`, separately from the runtime.
Run `uv sync --locked --all-packages` first. Transitional provisioning commands
require this source checkout: run `python -m playground_infra.ci configure` from
the repository root, and Pulumi from this `infra` directory. The Pulumi resource
names, state ownership and bootstrap script remain unchanged. Installed runtime
images contain no Pulumi/provider SDKs.

## Provisioning reference (no application activation)

The account/state setup below remains available for reviewed manual provisioning.
It does not activate a runtime, upload artifacts, or establish production readiness.

## 1. Prepare the accounts and domain

You need access to the domain's registrar, a Cloudflare account with R2 enabled,
a Hetzner Cloud project, and administrator access to the GitHub repository.
Merge these workflows into the repository's `master` default branch before
running them. Keep `PLAYGROUND_AUTO_DEPLOY` unset or `false` through PR1–PR3.

Prefer a dedicated registered domain and Cloudflare zone containing only playground
records. The runtime's certificate-renewal token can edit DNS throughout its
selected zone. A hostname such as `play.thesecret.dev` does not isolate permissions
from other records in `thesecret.dev`, including website and email records.

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

Compromise of the proxy or host could expose the renewal token and allow changes
to every DNS record in its zone. Before initial setup, verify the actual token
summary contains only the required permissions and selected zone, and review
which other services use that zone. Keep the token's identifier and scope with
its password-manager entry so it can be rotated or revoked independently.

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
the deployment private key and provisioning API tokens are not installed there.
Runtime deployment separately installs the Cloudflare certificate-renewal token
in private host configuration and exposes it only to the Traefik container.
Treat state access plus the passphrase, Hetzner project access, and trusted
deployment code as administrative access.

Change defaults through a reviewed PR before running setup. `sshPublicKey` can
add an administrator public key at host creation, and `sshCidrs` controls every
SSH client's allowed IPv4 networks. Narrowing it without a runner with a stable,
allowed egress address blocks GitHub-hosted deployment. Host-key and cloud-init
changes are not a routine way to update an already running host.

## 7. Provision only

Run **Actions → CLI playground setup → master** for explicitly authorized host
provisioning. Its protected `provision` job configures the existing R2-backed stack,
previews and applies infrastructure, then finishes. Keep `PLAYGROUND_AUTO_DEPLOY`
unset or false. Running the release workflow deliberately fails at its first step;
it does not publish images or deploy the coupled legacy runtime. PR4 replaces
this boundary with split-service release tooling and production E2E validation.

Keep the original state bucket and passphrase; do not replace state or disable
resource protection to recover an error. Setup retains protected host/IP resources,
pinned SSH identity, separate provisioning credentials and DNS-renewal credentials.
Backup/restore automation and cloud activation are outside this increment.

## Local checks

Build the real frontend before `uv run --locked pytest
tools/boxie/tests/test_packaging.py -m integration`. The required PR frontend job
runs that isolated-wheel/static-response/Compose check. The separate
[Linux sandbox workflow](../../../.github/workflows/boxie-lifecycle.yml)
checks native runsc security and both generic and product terminals through the
private API. Missing runsc is a failure, never a successful runc substitute.
