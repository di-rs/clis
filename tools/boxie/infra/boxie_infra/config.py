"""Write explicit operator configuration for the three private Boxie processes."""

import argparse
import json
from pathlib import Path
from uuid import UUID


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--installation-id", type=UUID, required=True)
    parser.add_argument("--host-id", required=True)
    parser.add_argument("--consumer-id", default="smoke")
    parser.add_argument("--orchestrator-url", default="http://orchestrator:8091")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--orchestrator-credential", default="/run/secrets/orchestrator")
    parser.add_argument("--consumer-credential", default="/run/secrets/consumer")
    parser.add_argument("--profiles", default="/etc/boxie/profiles.json")
    parser.add_argument("--api-url", default="http://api:8090")
    parser.add_argument("--postgres-dsn", default="/run/secrets/postgres-dsn")
    parser.add_argument("--temporal-address", default="temporal:7233")
    parser.add_argument("--temporal-server-name", default="temporal")
    parser.add_argument("--temporal-namespace", default="boxie")
    args = parser.parse_args()
    if not args.data_dir.is_absolute():
        parser.error("--data-dir must be an absolute daemon-host path")
    args.output.mkdir(parents=True, exist_ok=True)
    identity = {"installation_id": str(args.installation_id), "host_id": args.host_id}
    durable = {
        "postgres_dsn_file": args.postgres_dsn,
        "temporal_address": args.temporal_address,
        "temporal_server_name": args.temporal_server_name,
        "temporal_namespace": args.temporal_namespace,
        "temporal_ca_file": "/run/secrets/temporal-ca.pem",
        "temporal_cert_file": "/run/secrets/temporal-client.pem",
        "temporal_key_file": "/run/secrets/temporal-client.key",
    }
    configurations = {
        "orchestrator.json": {
            "data_dir": str(args.data_dir),
            "postgres_dsn_file": args.postgres_dsn,
            "api_url": args.api_url,
            "profiles_file": args.profiles,
            "credential_file": args.orchestrator_credential,
            "identity": {"installation_id": str(args.installation_id), "host_id": args.host_id},
            "bind_host": args.bind_host,
        },
        "api.json": {
            "release": "local",
            "identity": identity,
            "durable": durable,
            "orchestrator_url": args.orchestrator_url,
            "orchestrator_credential_file": args.orchestrator_credential,
            "consumer_credential_files": {args.consumer_id: args.consumer_credential},
            "bind_host": args.bind_host,
        },
    }
    configurations["worker.json"] = {
        "identity": identity,
        "durable": durable,
        "orchestrator_url": args.orchestrator_url,
        "orchestrator_credential_file": args.orchestrator_credential,
    }
    for name, value in configurations.items():
        (args.output / name).write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    main()
