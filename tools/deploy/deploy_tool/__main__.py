"""Thin command orchestration; provisioning is intentionally a separate program."""

import argparse
import json
import sys
from pathlib import Path

from .manifests import ReleaseError, load, read_json
from .prepare import load_prepared, prepare


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and activate one immutable component release"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "prepare"):
        command = sub.add_parser(name)
        command.add_argument("--descriptor", type=Path, required=True)
        command.add_argument("--release", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--environment", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
    deploy = sub.add_parser("deploy")
    deploy.add_argument("--prepared", type=Path, required=True)
    deploy.add_argument("--profile", type=Path, required=True)
    deploy.add_argument("--identity", type=Path, required=True)
    deploy.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()
    result: dict[str, object]
    try:
        if args.command == "deploy":
            from .transport import deploy_ssh

            result = dict(
                deploy_ssh(
                    load_prepared(args.prepared),
                    read_json(args.profile),
                    args.identity,
                    args.timeout,
                )
            )
        else:
            manifest = load(args.descriptor, args.release)
            if args.command == "validate":
                result = {
                    "component": manifest.component,
                    "revision": manifest.release["revision"],
                    "valid": True,
                }
            else:
                release_id = prepare(manifest, read_json(args.environment), args.output)
                result = {"component": manifest.component, "release_id": release_id}
        print(json.dumps(result, sort_keys=True))
        return 0
    except ReleaseError as error:
        print(f"deploy: {error}", file=sys.stderr)
        return 2
    except OSError, ValueError, TypeError, KeyError:
        print("deploy: invalid or inaccessible release input", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
