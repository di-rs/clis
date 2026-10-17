"""Render the fixed installation's private owner settings without printing them."""

import argparse
import json
import os
from pathlib import Path


def write_environment(path: Path, values: dict[str, str]) -> None:
    for name, value in values.items():
        if any(character in value for character in "\n\r\x00'$`\\"):
            raise ValueError(f"Invalid installation setting: {name}")
    path.write_text("".join(f"{key}='{value}'\n" for key, value in values.items()))
    path.chmod(0o600)


def render(root: Path, source: Path) -> None:
    credentials = root / "boxie"
    tls = root / "tls"

    def secret(name: str) -> str:
        return (credentials / name).read_text().strip()

    values = {
        "BOXIE_RUNTIME_IMAGE": os.environ["BOXIE_RUNTIME_IMAGE"],
        "BOXIE_DATA_DIR": "/srv/clis/boxie",
        "BOXIE_API_CONFIG": str(credentials / "api.json"),
        "BOXIE_ORCHESTRATOR_CONFIG": str(credentials / "orchestrator.json"),
        "BOXIE_PROFILES_FILE": str(credentials / "profiles.json"),
        "BOXIE_ORCHESTRATOR_CREDENTIAL_FILE": str(credentials / "orchestrator.secret"),
        "BOXIE_CONSUMER_CREDENTIAL_FILE": str(credentials / "consumer.secret"),
        "BOXIE_POSTGRES_DSN_FILE": str(credentials / "postgres-dsn.secret"),
        "BOXIE_TEMPORAL_CA_FILE": str(tls / "api/frontend-ca.pem"),
        "BOXIE_API_TEMPORAL_CERT_FILE": str(tls / "api/boxie-api.pem"),
        "BOXIE_API_TEMPORAL_KEY_FILE": str(tls / "api/boxie-api.key"),
        "BOXIE_ORCHESTRATOR_TEMPORAL_CERT_FILE": str(tls / "orchestrator/boxie-orchestrator.pem"),
        "BOXIE_ORCHESTRATOR_TEMPORAL_KEY_FILE": str(tls / "orchestrator/boxie-orchestrator.key"),
        "BOXIE_CONTROL_NETWORK": "boxie-control",
        "BOXIE_CONSUMER_NETWORK": "boxie-consumers",
        "TEMPORAL_CERTS_DIR": str(tls / "server"),
        "TEMPORAL_OPERATOR_CERTS_DIR": str(tls / "operator"),
        "TEMPORAL_UI_CERTS_DIR": str(tls / "ui"),
        "BOXIE_HOST_ID": secret("host-id"),
        "BOXIE_INSTALLATION_ID": secret("installation-id"),
        "BOXIE_TEMPORAL_NAMESPACE": "boxie",
        "OBS_COLLECTOR_IMAGE": os.environ["OBS_COLLECTOR_IMAGE"],
        "OBS_JOURNAL_GROUP_GID": os.environ["OBS_JOURNAL_GROUP_GID"],
        "OBS_GRAFANA_HOSTNAME": os.environ["OPERATOR_GRAFANA_HOSTNAME"],
        "OBS_GRAFANA_PLUGIN_DIR": str(root / "grafana-plugins"),
        "OBS_GRAFANA_ADMIN_PASSWORD_FILE": str(root / "grafana-admin"),
        "OBS_PROMETHEUS_CONFIG_FILE": str(root / "prometheus.yml"),
        "OPERATOR_CONFIG_FILE": str(root / "cloudflared.yml"),
        "OPERATOR_CREDENTIALS_FILE": str(root / "tunnel.json"),
    }
    for variable, name in (
        ("POSTGRES_PASSWORD", "postgres-password"),
        ("BOXIE_DB_PASSWORD", "boxie-password"),
        ("TEMPORAL_DB_PASSWORD", "temporal-password"),
        ("VISIBILITY_DB_PASSWORD", "visibility-password"),
    ):
        values[variable] = secret(name)
    write_environment(root / "boxie.env", values)
    write_environment(
        root / "playground.env",
        {
            "PLAYGROUND_IMAGE": os.environ["PLAYGROUND_IMAGE"],
            "PLAYGROUND_DOMAIN": os.environ["PLAYGROUND_DOMAIN"],
            "PLAYGROUND_DEPLOY_TOKEN": secret("preview-token"),
            "PLAYGROUND_BOXIE_URL": "http://boxie-api:8090",
            "PLAYGROUND_BOXIE_CREDENTIAL_FILE": str(credentials / "consumer.secret"),
            "PLAYGROUND_CONSUMER_NETWORK": "boxie-consumers",
            "PLAYGROUND_PROXY_NETWORK": "playground-proxy",
            "PLAYGROUND_DATA_VOLUME": "playground-data",
            "PLAYGROUND_CERTIFICATES_VOLUME": "playground-certificates",
            "ACME_EMAIL": os.environ["ACME_EMAIL"],
            "CLOUDFLARE_DNS_API_TOKEN": os.environ["CLOUDFLARE_DNS_API_TOKEN"],
        },
    )
    profiles_path = credentials / "profiles.json"
    profiles = json.loads(profiles_path.read_text()) if profiles_path.exists() else {}
    profile = json.loads(
        (source / "tools/cli-playground/infra/profiles/playground.json").read_text()
    )["playground"]
    profile["image_digest"] = os.environ["PLAYGROUND_SANDBOX_IMAGE"]
    profiles["playground"] = profile
    profiles_path.write_text(json.dumps(profiles, indent=2) + "\n")
    prometheus = (source / "tools/boxie/infra/observability/prometheus.yml").read_text()
    for key in ("BOXIE_INSTALLATION_ID", "BOXIE_HOST_ID", "BOXIE_TEMPORAL_NAMESPACE"):
        prometheus = prometheus.replace("${" + key + "}", values[key])
    (root / "prometheus.yml").write_text(prometheus)
    (root / "prometheus.yml").chmod(0o644)
    ingress = (source / "tools/boxie/infra/ingress/cloudflared.yml").read_text()
    for key in (
        "OPERATOR_TUNNEL_ID",
        "OPERATOR_TEAM_NAME",
        "OPERATOR_GRAFANA_HOSTNAME",
        "OPERATOR_TEMPORAL_HOSTNAME",
        "OPERATOR_GRAFANA_AUDIENCE",
        "OPERATOR_TEMPORAL_AUDIENCE",
    ):
        ingress = ingress.replace("${" + key + "}", os.environ[key])
    (root / "cloudflared.yml").write_text(ingress)
    (root / "cloudflared.yml").chmod(0o644)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    render(Path("/etc/clis"), args.source)


if __name__ == "__main__":
    main()
