# Environment monitoring wiring

The [native Boxie monitoring assets](../../tools/boxie/infra/observability/README.md)
own the collector, dashboards, image pins, metric filters and retention defaults.
The thin single-host environment exports nonsecret network, volume, identity and
disk handles. Render those handles into a new copy of the current native assets:

```sh
uv run --locked python -m observability_infra.render \
  --settings /private/observability-settings.json \
  --assets tools/boxie/infra/observability \
  --collector-image ghcr.io/example/collector@sha256:APPROVED_DIGEST \
  --journal-gid-file /private/journal-gid \
  --grafana-url https://grafana.example.com \
  --output /private/monitoring-release
```

The rendered Compose file uses platform-owned external volumes. Complete the
private plugin and Grafana secret paths documented in the native environment
example, and set `OBS_PROMETHEUS_CONFIG_FILE` to the rendered Prometheus file.
Use Boxie's and Temporal's optional monitoring overlays with matching private
network handles. The renderer does not build images, create cloud resources or
activate services. Playground currently has no metrics exporter; environment
composition does not add an unavailable scrape target.
