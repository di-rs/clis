# Operator ingress

`ingress_infra.access` owns two exact Cloudflare Access applications, their inline
email allow policies, a locally configured tunnel and two proxied CNAME records.
It declares resources and returns values; it never applies resources, writes
credentials, starts containers or discovers product configuration.

```python
from ingress_infra.access import (
    OperatorIngressConfig,
    OperatorOrigin,
    OperatorOrigins,
    provision_operator_access,
)

config = OperatorIngressConfig(
    team_name="operators",
    grafana_hostname="grafana.example.com",
    temporal_hostname="temporal.example.com",
    allowed_identities=("operator@example.com",),
)
origins = OperatorOrigins(
    OperatorOrigin("http://grafana:3000", "owned-grafana-origin"),
    OperatorOrigin("http://temporal-ui:8080", "owned-temporal-origin"),
)
```

Pass those values to `provision_operator_access(config, origins, *, account_id,
zone_id, tunnel_name, tunnel_secret, credentials_file, provider=None)`.
`account_id` and `zone_id` are explicit 32-character lowercase hex identifiers;
`tunnel_secret` is an environment-owned, stable Pulumi secret encoding at least
32 random bytes as base64. Never generate a new secret on each preview. The
optional provider is a configured Cloudflare provider; default provider
credentials also work. No credentials are obtained from Playground.

The frozen `OperatorIngressOutputs` returns `tunnel_id`, `grafana_audience`,
`temporal_audience`, `cloudflared_config` and secret `tunnel_credentials_json`,
all as Pulumi outputs. Each hostname uses its own application's `.aud`, never
its resource ID. The pure `render_cloudflared` transform rejects empty or shared
audiences and invalid tunnel IDs. Ordinary input validation precedes resource
registration; derived output validation has no filesystem or activation effects.
Mock tests establish these declarations, not live authorization.

The release owner materializes the secret output privately at a host path and
sets `OPERATOR_CREDENTIALS_FILE`; do not export its bytes in logs, ordinary stack
outputs or environment files. With this Compose file pass
`credentials_file="/run/secrets/tunnel.json"`. The container runs as **65532:65532**;
install that credential file owned by 65532:65532, mode 0400, with traversable
parents. `OPERATOR_CONFIG_FILE` references the rendered nonsecret YAML; mode
0444 or equivalent reader access is sufficient. Both mounts are read-only.
[cloudflared.yml](cloudflared.yml) documents the shape only and contains unusable
placeholders. The validated output is the deployable configuration.

Set `OPERATOR_GRAFANA_NETWORK=origins.grafana.network` and
`OPERATOR_TEMPORAL_NETWORK=origins.temporal.network`. Platform composition owns
these **separate internal frontend networks**, connects only each dashboard and
this tunnel to each, and passes their exact handles. A name alone cannot prove a
Docker network's membership: inspect actual membership during activation. The
tunnel's third network is its own outbound bridge. It never joins control,
metrics, ingest or query networks, mounts Docker, or publishes host ports.
Grafana may connect to its own private query network; Temporal UI retains its
private control/backend connection. No tunnel route exposes Boxie, agents,
Docker, PostgreSQL, Temporal RPC, Prometheus or VictoriaLogs. Unmatched hosts
terminate at `http_status:404`.

Grafana's separately owned render input `grafana_public_url` must equal
`https://` plus `config.grafana_hostname`. Its own login remains enabled and its
admin password is independently mounted; Access does not replace that login.
Existing DNS records require an ownership/import decision before applying these
declarations. Do not create an additional public A record or invoke imperative
`tunnel route dns` for these names.

Run declaration/config tests with:

```sh
uv run --locked pytest infra/ingress/tests/test_operator_boundary.py
```

Before activation, validate the rendered config with the selected immutable
cloudflared 2026.10.0 image from [Compose](compose.yml):

```sh
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate
cloudflared tunnel --config /etc/cloudflared/config.yml ingress rule https://grafana.example.com
cloudflared tunnel --config /etc/cloudflared/config.yml ingress rule https://temporal.example.com
cloudflared tunnel --config /etc/cloudflared/config.yml ingress rule https://unmatched.example.com
```

Treat warnings or unused keys as failure even when exit status is zero. These
checks prove parsing and route matching only. Live allowed/denied identities,
missing/wrong-audience JWT rejection, DNS ownership, tunnel connectivity and
absence of direct-origin access remain authorized-environment activation gates.
The origin checks use [Cloudflare Access validation](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/configure-tunnels/origin-parameters/#access).
