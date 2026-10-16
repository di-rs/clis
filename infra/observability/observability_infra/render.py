"""Render owner assets using the thin environment's nonsecret deployment handles."""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from observability_infra.config import ObservabilityConfig, ScrapeEndpoint, render_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--collector-image", required=True)
    parser.add_argument("--journal-gid-file", type=Path, required=True)
    parser.add_argument("--grafana-url", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        settings: dict[str, Any] = json.loads(args.settings.read_text())
        allowed = {
            "environment",
            "host",
            "metrics_network",
            "grafana_origin_network",
            "vector_volume",
            "logs_volume",
            "prometheus_volume",
            "grafana_volume",
            "backing_mounts",
            "consumer_scrapes",
        }
        if not isinstance(settings, dict) or settings.keys() != allowed:
            raise ValueError("invalid environment settings")
        settings["backing_mounts"] = tuple(settings["backing_mounts"])
        settings["consumer_scrapes"] = tuple(
            ScrapeEndpoint(**item) for item in settings["consumer_scrapes"]
        )
        config = ObservabilityConfig(
            assets_dir=args.assets,
            collector_image=args.collector_image,
            journal_group_gid=int(args.journal_gid_file.read_text().strip()),
            grafana_public_url=args.grafana_url,
            **settings,
        )
        result = render_config(config, args.output)
    except OSError, ValueError, TypeError, KeyError, AttributeError:
        print("Cannot render observability: check explicit deployment inputs", file=sys.stderr)
        return 2
    print(json.dumps({"rendered_files": len(result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
