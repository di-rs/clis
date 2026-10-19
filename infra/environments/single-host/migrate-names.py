"""Copy stopped installation configuration to a new tree for the naming cutover."""

import argparse
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path

# Legacy names belong only to this one-time migration boundary.
FILES = {
    "playground.env": "cli-playground.env",
    "playground-image.env": "cli-playground-image.env",
    "boxie/orchestrator.json": "boxie/supervisor.json",
    "boxie/orchestrator.secret": "boxie/supervisor.secret",
}
JSON_FILES = {"boxie/api.json", "boxie/orchestrator.json", "boxie/profiles.json"}
VALUES = {
    "playground": "cli-playground",
    "playground-proxy": "cli-playground-proxy",
    "playground-data": "cli-playground-data",
    "playground-certificates": "cli-playground-certificates",
    "/opt/playground/shell.sh": "/opt/cli-playground/shell.sh",
    "/run/secrets/orchestrator": "/run/secrets/supervisor",
    "/etc/boxie/orchestrator.json": "/etc/boxie/supervisor.json",
    "http://orchestrator:8091": "http://supervisor:8091",
}


def rename_key(key: str) -> str:
    if key == "PLAYGROUND_IMAGE":
        return "CLI_PLAYGROUND_BACKEND_IMAGE"
    if key.startswith("PLAYGROUND_"):
        return "CLI_" + key
    if key == "BOXIE_RUNTIME_IMAGE":
        return "BOXIE_IMAGE"
    return key.replace("ORCHESTRATOR", "SUPERVISOR").replace("orchestrator_", "supervisor_")


def rename_value(value: str) -> str:
    if value in VALUES and value != "playground":
        return VALUES[value]
    for old, new in (
        ("/opt/sanctuary/playground", "/opt/sanctuary/cli-playground"),
        ("/srv/sanctuary/playground", "/srv/sanctuary/cli-playground"),
        ("/etc/sanctuary/boxie/orchestrator.", "/etc/sanctuary/boxie/supervisor."),
        (
            "/etc/sanctuary/tls/orchestrator/boxie-orchestrator.",
            "/etc/sanctuary/tls/supervisor/boxie-supervisor.",
        ),
    ):
        if value.startswith(old):
            return new + value[len(old) :]
    return value


def migrate_environment(content: str) -> str:
    lines = []
    names = set()
    for line in content.splitlines(keepends=True):
        if not line.strip() or line.lstrip().startswith("#"):
            lines.append(line)
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=(.*?)(\r?\n)?", line)
        if match is None:
            raise ValueError("Unsupported environment assignment; review configuration manually")
        old, value, newline = match.groups()
        name = rename_key(old)
        if name in names:
            raise ValueError(f"Conflicting environment key: {name}")
        names.add(name)
        # Secret bytes and image digests are opaque. Only path/identity settings change.
        if name.endswith(
            ("_FILE", "_CONFIG", "_DIR", "_VOLUME", "_NETWORK", "_PROFILE", "_URL", "_PROJECT_NAME")
        ):
            quote = value[0] if value.startswith(("'", '"')) else ""
            if quote and not value.endswith(quote):
                raise ValueError(f"Invalid quoted setting: {name}")
            raw = value[1:-1] if quote else value
            renamed = (
                VALUES.get(raw, raw)
                if name.endswith(("_PROFILE", "_PROJECT_NAME"))
                else rename_value(raw)
            )
            value = quote + renamed + quote
        lines.append(f"{name}={value}{newline or ''}")
    return "".join(lines)


def renamed_fields(values: dict[str, object], names: dict[str, str]) -> dict[str, object]:
    result = dict(values)
    for old, new in names.items():
        if old in result:
            if new in result:
                raise ValueError(f"Conflicting configuration key: {new}")
            result[new] = result.pop(old)
    return result


def migrate_json(value: object, relative: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("Configuration must contain a JSON object")
    result: dict[str, object] = dict(value)
    if relative == "boxie/api.json":
        result = renamed_fields(
            result,
            {
                "orchestrator_url": "supervisor_url",
                "orchestrator_credential_file": "supervisor_credential_file",
            },
        )
        for key in ("supervisor_url", "supervisor_credential_file"):
            if isinstance(result.get(key), str):
                result[key] = rename_value(str(result[key]))
        consumers = result.get("consumer_credential_files")
        if isinstance(consumers, dict):
            result["consumer_credential_files"] = renamed_fields(
                consumers, {"playground": "cli-playground"}
            )
    elif relative == "boxie/orchestrator.json":
        credential = result.get("credential_file")
        if isinstance(credential, str):
            result["credential_file"] = rename_value(credential)
    elif relative == "boxie/profiles.json":
        result = renamed_fields(result, {"playground": "cli-playground"})
        profile = result.get("cli-playground")
        if "playground" in value and isinstance(profile, dict):
            commands = profile.get("commands")
            if isinstance(commands, list):
                profile["commands"] = [
                    ["/opt/cli-playground/shell.sh"]
                    if command == ["/opt/playground/shell.sh"]
                    else command
                    for command in commands
                ]
    return result


def sync_path(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def migrate(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise ValueError("Source must not be a symlink")
    source = source.resolve(strict=True)
    if source.stat().st_mode & 0o077:
        raise ValueError("Configuration directory must be private")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError("Destination already exists; choose an unused staging path")
    if source == destination.resolve() or source in destination.resolve().parents:
        raise ValueError("Destination must be outside the source tree")
    entries = [source, *source.rglob("*")]
    for path in entries:
        if path.is_symlink() or not (path.is_dir() or path.is_file()):
            raise ValueError("Configuration contains a symlink or special file")
        if (
            path.is_file()
            and (
                path.suffix in {".env", ".secret", ".key"}
                or path.name.endswith(("-password", "-token", "-id"))
                or path.name in {"grafana-admin", "tunnel.json"}
            )
            and path.stat().st_mode & 0o077
        ):
            raise ValueError("Secret file permissions are not private")
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        for path in entries:
            relative = path.relative_to(source).as_posix()
            target = staging / FILES.get(relative, relative)
            if path != source and target.exists():
                raise ValueError(f"Conflicting configuration path: {relative}")
            if path.is_dir():
                target.mkdir(exist_ok=path == source)
            else:
                content = path.read_bytes()
                if path.suffix == ".env":
                    content = migrate_environment(content.decode()).encode()
                elif relative in JSON_FILES:
                    content = (
                        json.dumps(migrate_json(json.loads(content), relative), indent=2) + "\n"
                    ).encode()
                elif relative == "prometheus.yml":
                    content = re.sub(
                        rb"(?m)^(\s*-\s*(?:job_name:\s*)?)boxie-orchestrator(-sdk|:9103|:9203)?$",
                        rb"\1boxie-supervisor\2",
                        content,
                    )
                target.write_bytes(content)
                if target.read_bytes() != content:
                    raise OSError("Configuration copy verification failed")
            metadata = path.stat()
            if (target.stat().st_uid, target.stat().st_gid) != (metadata.st_uid, metadata.st_gid):
                os.chown(target, metadata.st_uid, metadata.st_gid)
            target.chmod(stat.S_IMODE(metadata.st_mode))
            if target.is_file():
                sync_path(target)
        for directory in reversed([staging, *[p for p in staging.rglob("*") if p.is_dir()]]):
            sync_path(directory)
        staging.rename(destination)
        sync_path(destination.parent)
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    migrate(args.source, args.destination)


if __name__ == "__main__":
    main()
