# Boxie infrastructure

This directory builds and deploys the Boxie API, supervisor, and sandbox image.
It also owns the PostgreSQL, Temporal, monitoring, and dashboard-access
configuration used by the service.

## Start here

For the shared production host, follow [host setup and releases](../../../infra/environments/single-host/README.md).
For a disposable native Linux machine, follow [local setup](local-setup.md).
Both require the [host prerequisites](host-requirements.md), including gVisor,
database migrations, Temporal setup, and private service credentials.

Docker Desktop is useful for image builds and configuration checks. Running a
sandbox requires `runsc`; Boxie does not fall back to a weaker runtime.

## Deployment boundaries

The API accepts private consumer traffic. The supervisor alone receives Docker
access and artifact storage. The playground is an optional, separate consumer.
Keep its Compose project name distinct from Boxie's `BOXIE_PROJECT_NAME`
(default `boxie`); a shared project-name override can make cleanup affect both.

Database, Temporal, and monitoring projects have separate maintenance lifecycles.
An ordinary Boxie release migrates its database schema and restarts the API and
supervisor without upgrading those services.

| Component | Setup and operations |
| --- | --- |
| PostgreSQL | [Databases, migrations, and retention](postgres/README.md) |
| Temporal | [Certificates, namespace, and schema operations](temporal/README.md) |
| Monitoring | [Logs, metrics, and storage budgets](observability/README.md) |
| Operator access | [Protected Grafana and Temporal dashboards](ingress/README.md) |

Use the [validation guide](validation.md) to distinguish packaging checks from
native Linux isolation and lifecycle checks. A successful image build does not
prove the deployment is ready.
