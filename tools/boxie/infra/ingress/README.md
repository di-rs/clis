# Protected operator dashboards

Cloudflared's native Access validation protects exactly two origins: Grafana and
Temporal UI. Each exact hostname has its own Cloudflare Access application and
audience, with an explicit operator allow policy. A final ingress rule returns
404. Boxie's API, metrics and log ingestion are not routed. Neither origin has
published Docker ports; the tunnel reaches them over separate internal bridges.

Create the two self-hosted Access applications and a locally managed tunnel in
Cloudflare. Record the team name, distinct application audiences and tunnel UUID.
Create DNS tunnel routes for the two dashboard hostnames. Keep the tunnel JSON
credentials outside the checkout in a private file readable by UID 65532. Grafana
also needs its own admin secret, configured by the monitoring deployment.

The native config is a template with six nonsecret substitutions. Export the
following values, using exact hostnames and separate nonempty audience IDs:

```sh
export OPERATOR_TUNNEL_ID=00000000-0000-0000-0000-000000000001
export OPERATOR_TEAM_NAME=operators
export OPERATOR_GRAFANA_HOSTNAME=grafana.example.com
export OPERATOR_TEMPORAL_HOSTNAME=temporal.example.com
export OPERATOR_GRAFANA_AUDIENCE=replace-with-grafana-application-audience
export OPERATOR_TEMPORAL_AUDIENCE=replace-with-temporal-application-audience
export OPERATOR_CONFIG_FILE=/private/boxie/cloudflared.yml
export OPERATOR_CREDENTIALS_FILE=/private/boxie/tunnel.json
: "${OPERATOR_GRAFANA_AUDIENCE:?}" "${OPERATOR_TEMPORAL_AUDIENCE:?}"
test "$OPERATOR_GRAFANA_AUDIENCE" != "$OPERATOR_TEMPORAL_AUDIENCE"
envsubst '${OPERATOR_TUNNEL_ID} ${OPERATOR_TEAM_NAME} ${OPERATOR_GRAFANA_HOSTNAME} ${OPERATOR_TEMPORAL_HOSTNAME} ${OPERATOR_GRAFANA_AUDIENCE} ${OPERATOR_TEMPORAL_AUDIENCE}' \
  < tools/boxie/infra/ingress/cloudflared.yml > "$OPERATOR_CONFIG_FILE"
docker compose -f tools/boxie/infra/ingress/compose.yml \
  run --rm cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
docker compose -f tools/boxie/infra/ingress/compose.yml up -d
```

The [monitoring setup](../observability/README.md) creates the three shared private
bridges. Its optional Temporal overlay attaches the UI to the tunnel bridge.
Grafana's root URL must be `https://` plus the same Grafana hostname; secure
cookies and local Grafana login remain enabled behind Access. No credentials are
embedded in YAML. Changing Access policies belongs to the Cloudflare operator;
there is no separate Python infrastructure distribution or JWT verifier.

At activation, test both origins with unauthenticated, wrong-audience and valid
operator requests through the real tunnel. Also test an unconfigured hostname
and verify direct origins are unreachable externally. The local native ingress
validator checks configuration shape; it does not prove live Access authorization.


## Independently targeted release

Use the protected manual owner workflow with a reviewed full commit SHA and unit
`ingress`. The fixed operator command uses this directory's native Compose files,
approved immutable images and private owner inputs. It excludes schema/bootstrap
and storage-init jobs, records native pins and committed configuration hashes,
and checks readiness. See the [single-host procedure](../../../../infra/environments/single-host/README.md)
for host tooling, credential readers, selection and activation limits.

`boxie_infra.access` contains only the necessary cloud declarations and inert
IDs/audiences/credentials. Native `cloudflared.yml` remains the single routing
policy. The private `/ready` probe verifies tunnel connectivity, while real
Access authorization and direct-origin denial remain activation checks. Its
metrics listener has no published host port. Credentials must be readable by
UID 65532 from their private parent directory.
