# Operator dashboard access

This tunnel gives operators access to Grafana and Temporal UI through Cloudflare
Access. Each dashboard has its own hostname, Access application, and audience ID.
The dashboards stay on private networks; the tunnel does not expose Boxie's API,
metrics, or log ingestion. Unknown hostnames return 404.

## Prepare access

Create the two self-hosted Access applications and a locally managed tunnel in
Cloudflare. Record the team name, distinct application audiences and tunnel UUID.
Create DNS tunnel routes for the two dashboard hostnames. Keep the tunnel JSON
credentials outside the checkout in a private file readable by UID 65532. Grafana
also needs its own admin secret, configured by the monitoring deployment.

## Configure the tunnel

The config template takes six nonsecret values. Export the
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
  < packages/boxie/infra/ingress/cloudflared.yml > "$OPERATOR_CONFIG_FILE"
docker compose -f packages/boxie/infra/ingress/compose.yml \
  run --rm cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
docker compose -f packages/boxie/infra/ingress/compose.yml up -d
```

The [monitoring setup](../observability/README.md) creates the three shared private
bridges. Its optional Temporal overlay attaches the UI to the tunnel bridge.
Grafana's root URL must be `https://` plus the same Grafana hostname; secure
cookies and local Grafana login remain enabled behind Access. No credentials are
embedded in YAML. Changing Access policies belongs to the Cloudflare operator;
cloudflared validates the Access tokens.

## Verify access

Test both origins with unauthenticated, wrong-audience and valid
operator requests through the real tunnel. Also test an unconfigured hostname
and verify direct origins are unreachable externally. The local native ingress
validator checks configuration shape; it does not prove live Access authorization.
