"""Write explicit operator configuration for the two private Boxie processes."""

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
    parser.add_argument("--agent-url", default="http://agent:8091")
    parser.add_argument("--bind-host", default="0.0.0.0")
    parser.add_argument("--agent-credential", default="/run/secrets/agent")
    parser.add_argument("--consumer-credential", default="/run/secrets/consumer")
    parser.add_argument("--profiles", default="/etc/boxie/profiles.json")
    args = parser.parse_args()
    if not args.data_dir.is_absolute():
        parser.error("--data-dir must be an absolute daemon-host path")
    args.output.mkdir(parents=True, exist_ok=True)
    configurations = {
        "agent.json": {
            "data_dir": str(args.data_dir),
            "profiles_file": args.profiles,
            "credential_file": args.agent_credential,
            "identity": {"installation_id": str(args.installation_id), "host_id": args.host_id},
            "bind_host": args.bind_host,
            "max_sandboxes": 8,
        },
        "api.json": {
            "release": "local",
            "agent_url": args.agent_url,
            "agent_credential_file": args.agent_credential,
            "consumer_credential_files": {args.consumer_id: args.consumer_credential},
            "bind_host": args.bind_host,
        },
    }
    for name, value in configurations.items():
        (args.output / name).write_text(json.dumps(value, indent=2) + "\n")


if __name__ == "__main__":
    main()
