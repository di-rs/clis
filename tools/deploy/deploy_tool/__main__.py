"""Thin command orchestration; provisioning is intentionally a separate program."""

import argparse
import json
import sys
from pathlib import Path

from .manifests import ReleaseError, load, read_json, source_manifest
from .prepare import canonical, load_prepared, prepare, private_write


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare and activate one immutable component release"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "prepare"):
        command = sub.add_parser(name)
        command.add_argument("--repository", type=Path, required=True)
        command.add_argument("--descriptor", type=Path, required=True)
        command.add_argument("--release", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--environment", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
    public = sub.add_parser("manifest")
    for flag in ("repository", "descriptor", "images", "settings", "output"):
        public.add_argument("--" + flag, type=Path, required=True)
    public.add_argument("--revision", required=True)
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
        elif args.command == "manifest":
            manifest = source_manifest(
                args.repository,
                args.descriptor.as_posix(),
                args.revision,
                read_json(args.images),
                read_json(args.settings),
            )
            private_write(args.output, canonical(manifest.release))
            result = {"component": manifest.component, "valid": True}
        else:
            manifest = load(args.descriptor, args.release, args.repository)
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
