# Operator ingress provisioning

`ingress_infra.access` declares distinct Cloudflare Access applications for
Grafana and Temporal UI when selected by the
[single-host environment](../environments/single-host/README.md). Each origin
receives its own private network and Access audience. Tunnel credentials remain
secret outputs, separate from nonsecret configuration.

The [native ingress guide](../../tools/boxie/infra/ingress/README.md) documents
activation and verification against an explicitly authorized environment.
