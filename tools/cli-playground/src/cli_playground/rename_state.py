"""Copy drained application state for the component-name maintenance cutover."""

import argparse
import fcntl
import filecmp
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, TypeAdapter

from cli_playground.boxie_client import SandboxView
from cli_playground.session_store import SessionBinding, sync_directory


class CleanupReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    view: SandboxView
    cleaned_at: AwareDatetime


def rename_reason(data: dict[str, object]) -> bool:
    reason = data.get("reason")
    if reason in ("orchestrator_restart", "orchestrator_shutdown"):
        data["reason"] = str(reason).replace("orchestrator_", "supervisor_", 1)
        return True
    return False


def inventory(root: Path) -> dict[Path, tuple[int, int, int]]:
    result = {}
    for path in (root, *root.rglob("*")):
        metadata = path.lstat()
        mode = metadata.st_mode
        if not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
            raise ValueError("state must contain only regular files and directories")
        result[path.relative_to(root)] = (mode, metadata.st_uid, metadata.st_gid)
    return result


def migrate(source: Path, destination: Path, receipts: Path | None = None) -> None:
    """Keep the source intact; publish a complete copy or reject a conflicting target.

    All application writers must be stopped before calling this function. The
    lock excludes another converter, not an application process.
    """
    if source.is_symlink() or destination.is_symlink():
        raise ValueError("state roots must be regular directories")
    source, destination = source.resolve(), destination.resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("source and destination must be separate trees")
    if not source.is_dir():
        raise ValueError("source directory does not exist")
    source_files = inventory(source)
    cleaned = {}
    if receipts is not None:
        records = json.loads(receipts.read_bytes())
        for record in records:
            rename_reason(record["view"])
        for receipt in TypeAdapter(list[CleanupReceipt]).validate_python(records):
            view = receipt.view
            if view.id in cleaned:
                raise ValueError("conflicting cleanup receipts")
            if view.state not in {"terminated", "failed"} or view.cleanup_pending:
                raise ValueError("drain sandboxes before exporting cleanup receipts")
            cleaned[view.id] = view
    operations = source / ".operations"
    for name in ("creates", "uploads", "retirements"):
        for path in (operations / name).glob("*"):
            if name != "retirements" or path.suffix != ".done":
                raise ValueError("drain pending application operations before migration")
    replacements: dict[Path, bytes] = {}
    for path in (operations / "bindings").glob("*.json"):
        data = json.loads(path.read_bytes())
        changed = rename_reason(data["sandbox"])
        binding = SessionBinding.model_validate(data)
        if receipts is not None:
            final_view = cleaned.get(binding.sandbox.id)
            if final_view is not None:
                if (
                    final_view.created_at != binding.sandbox.created_at
                    or final_view.absolute_deadline != binding.sandbox.absolute_deadline
                ):
                    raise ValueError("mismatched cleanup receipt")
                data.update(
                    sandbox=final_view.model_dump(mode="json"),
                    pending_stop=False,
                    attached=False,
                    attachment_deadline=None,
                )
                binding = SessionBinding.model_validate(data)
                changed = True
            # SQL purges cleaned rows after seven days; terminal cached views remain valid.
        if (
            binding.sandbox.state not in {"terminated", "failed"}
            or binding.sandbox.cleanup_pending
            or binding.pending_stop
        ):
            raise ValueError("drain and confirm sandbox cleanup before migration")
        if changed:
            replacements[path.relative_to(source)] = json.dumps(data).encode() + b"\n"
    lock = destination.with_name(f".{destination.name}.rename-lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "rb") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(
            prefix=".cli-playground-rename-", dir=destination.parent
        ) as tmp:
            staged = Path(tmp) / "state"
            shutil.copytree(source, staged)
            for relative, (_, uid, gid) in source_files.items():
                target = staged / relative
                if (target.stat().st_uid, target.stat().st_gid) != (uid, gid):
                    os.chown(target, uid, gid)
            for relative, content in replacements.items():
                (staged / relative).write_bytes(content)
            if inventory(staged) != source_files:
                raise ValueError("source changed during migration")
            if destination.exists():
                if inventory(destination) != source_files or any(
                    not filecmp.cmp(staged / relative, destination / relative, shallow=False)
                    for relative, (mode, _, _) in source_files.items()
                    if stat.S_ISREG(mode)
                ):
                    raise ValueError("destination conflicts with migrated state")
                return
            for relative, (mode, _, _) in source_files.items():
                if stat.S_ISREG(mode):
                    with (staged / relative).open("rb") as stream:
                        os.fsync(stream.fileno())
            for relative, (mode, _, _) in reversed(source_files.items()):
                if stat.S_ISDIR(mode):
                    sync_directory(staged / relative)
            staged.rename(destination)
            sync_directory(destination.parent)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="drained data directory; all writers stopped")
    parser.add_argument(
        "destination", type=Path, help="new data directory; original remains intact"
    )
    parser.add_argument(
        "--receipts", type=Path, help="cleanup receipts exported from stopped Boxie SQL"
    )
    args = parser.parse_args()
    migrate(args.source, args.destination, args.receipts)


if __name__ == "__main__":
    main()
