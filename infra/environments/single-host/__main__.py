"""Run from this directory after the repository's locked uv workspace sync."""

import pulumi
from environment import EnvironmentConfig, compose

config = pulumi.Config()
settings = EnvironmentConfig.model_validate(config.require_object("environment"))
environment = compose(
    settings,
    tunnel_secret=config.require_secret("operatorTunnelSecret") if settings.operators else None,
)
pulumi.export("connectionProfile", environment.host.connection_profile)
# One-time setup transfer to the deployment environment secret store; never publish with the profile.
pulumi.export("deploymentPrivateKey", environment.host.deployment_private_key)
pulumi.export("sharedResources", environment.resources)
pulumi.export("deploymentHandles", environment.handles)
pulumi.export("observabilitySettings", environment.observability_settings)
pulumi.export("boxie", environment.boxie.settings)
if environment.playground:
    pulumi.export("playground", environment.playground.settings)
if environment.operators:
    pulumi.export("operatorTunnelId", environment.operators.tunnel_id)
    pulumi.export("operatorGrafanaAudience", environment.operators.grafana_audience)
    pulumi.export("operatorTemporalAudience", environment.operators.temporal_audience)
    pulumi.export("operatorCredentials", environment.operators.tunnel_credentials_json)
