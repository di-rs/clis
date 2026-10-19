"""Convert a drained legacy execution journal into a separate file, retaining the original."""

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from boxie.persistence.atomic import sync_directory
from boxie.sandbox.execution.journal import JournalData


def rename_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    return {
        "orchestrator_restart": "supervisor_restart",
        "orchestrator_shutdown": "supervisor_shutdown",
    }.get(reason, reason)


def rename_spec(spec: dict[str, Any], *, application: bool) -> dict[str, Any]:
    result = dict(spec)
    if result.get("profile_id") == "playground":
        result["profile_id"] = "cli-playground"
    if application and result.get("command") == ["/opt/playground/shell.sh"]:
        result["command"] = ["/opt/cli-playground/shell.sh"]
    return result


def convert_journal(source: Path, destination: Path) -> None:
    if source.is_symlink() or destination.is_symlink() or source.resolve() == destination.resolve():
        raise ValueError("use separate, nonsymlink source and destination files")
    original = source.read_bytes()
    metadata = source.stat()
    data = json.loads(original)
    for record in data["records"]:
        application = record["consumer_id"] == "playground"
        if application:
            record["consumer_id"] = "cli-playground"
        record["reason"] = rename_reason(record.get("reason"))
        record["spec"] = rename_spec(record["spec"], application=application)
        # Historical snapshots must still match their immutable profile-version digest.
    journal = JournalData.model_validate(data)
    identifiers = [record.id for record in journal.records]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("conflicting duplicate sandbox identities")
    for record in journal.records:
        if record.identity != journal.identity:
            raise ValueError("execution journal installation mismatch")
        if (
            record.state not in {"terminated", "failed"}
            or not record.removal_confirmed
            or record.cleanup_pending
            or record.creation_uncertain
            or record.start_uncertain
            or record.attached
            or record.container_id is not None
        ):
            raise ValueError("drain sandboxes and confirm physical cleanup before conversion")
    converted = journal.model_dump_json().encode()
    if destination.exists():
        existing = destination.stat()
        if destination.read_bytes() == converted and (
            existing.st_uid,
            existing.st_gid,
            stat.S_IMODE(existing.st_mode),
        ) == (metadata.st_uid, metadata.st_gid, stat.S_IMODE(metadata.st_mode)):
            sync_directory(destination.parent)
            return
        raise FileExistsError("destination conflicts with converted journal")
    descriptor, temporary = tempfile.mkstemp(prefix=".rename-journal-", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            current = os.fstat(output.fileno())
            if (current.st_uid, current.st_gid) != (metadata.st_uid, metadata.st_gid):
                os.fchown(output.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(output.fileno(), stat.S_IMODE(metadata.st_mode))
            output.write(converted)
            output.flush()
            os.fsync(output.fileno())
        if source.read_bytes() != original:
            raise ValueError("source journal changed; stop all writers before conversion")
        os.link(temporary, destination)
        sync_directory(destination.parent)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    convert_journal(args.source, args.destination)


if __name__ == "__main__":
    main()
