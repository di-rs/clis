# Playground infrastructure and releases

Playground owns its browser product, private Boxie client, product DNS and Traefik
routes. It has no Docker socket, workflow worker or Temporal schema ownership.
Provision the optional product through the thin
[single-host environment](../../../infra/environments/single-host/README.md).
The shared host, disk, networks and persistent volumes belong to
[platform](../../../infra/platform/README.md).

## First installation

Complete the platform, Temporal, observability and Boxie installation before
activating Playground. Use the exported `deploymentHandles` and Playground
settings for the external consumer/proxy/metrics networks and data/certificate
volumes. Copy [environment.example.json](environment.example.json) to a private
file outside the checkout and fill every field using those outputs. Store the
server-side Boxie consumer credential on the host, owned by UID 10001 with mode
0600, and use its absolute path for `PLAYGROUND_BOXIE_CREDENTIAL_FILE`.
`PLAYGROUND_BOXIE_URL` is the supplied private API origin. The consumer credential
must match its Boxie configuration and is never delivered to the browser.

The production gateway runs as UID:GID 10001:10001. Platform prepares its data
volume with this ownership. Traefik receives only the certificate-renewal DNS
token and public hostname. The gateway receives only the product deployment
token and Boxie credential. Both service images are registry-qualified immutable
SHA-256 references in the release manifest. The pinned proxy digest is recorded
in [release.example.json](release.example.json); the application digest is
replaced by trusted CI after building the reviewed source revision.

For local development, build the frontend and image, configure the independent
Boxie endpoint, and use [compose.yml](compose.yml). This local build manifest is
separate from the immutable production release contract.

## Application releases

The [release descriptor](release-component.json) owns exactly `gateway` and
`proxy`. Use the generic [release tool](../../deploy/README.md):

```sh
uv run --locked python -m deploy_tool prepare \
  --descriptor tools/cli-playground/infra/release-component.json \
  --release /private/playground-release.json \
  --environment /private/playground-environment.json \
  --output /private/playground-prepared
uv run --locked python -m deploy_tool deploy \
  --prepared /private/playground-prepared \
  --profile /private/connection.json --identity /private/deployment-key
```

Run from the checkout matching the approved manifest revision. The deployment
key and environment input require mode 0600. Keep the prepared bundle private:
it contains resolved product secrets. The host must already have read-only
registry pull access if its application image is private. No CI write token is
sent to the host.

Releases use stable project `clis-playground` and reconcile only the two declared
services, without dependency upgrades or persistent-state deletion. The release
tool checks the exact running image IDs and Docker health before moving `current`.
Failed activation attempts restore a compatible previous application release;
first-install failure reports that no previous release exists. An SSH timeout
requires inspecting `/opt/clis/playground/status.json` before retrying.

## CI settings and operator checks

The protected GitHub environment `playground` holds variable `DEPLOYMENT_PROFILE`
(the nonsecret JSON profile) and secrets `DEPLOYMENT_SSH_KEY` and
`COMPONENT_ENVIRONMENT` (the private JSON environment). These are independent of
Pulumi/R2/cloud credentials. Manual release runs reviewed `master` code. Set
repository variable `PLAYGROUND_AUTO_DEPLOY=true` only after the first installation
passes its acceptance checks. Automatic deployment is otherwise disabled.

The [application workflow](../../../.github/workflows/cli-playground-release.yml)
builds the product image and calls the shared component release workflow. PR
binary builds and trusted preview publication remain separate workflows; a
preview publication never deploys the runtime or changes a Boxie profile.

After activation, verify public HTTPS/DNS/ACME, publish an approved native bundle,
open its browser terminal, publish a newer immutable revision, reset and expire
the preview. Run [playground_infra.smoke](playground_infra/smoke.py) on the native
Linux/gVisor environment. Local Compose/contract tests cannot prove public DNS,
Access protection, whole-host sizing or native sandbox isolation.
