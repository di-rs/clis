"""Offline cutover preserves preview identity and refuses undrained writers."""

import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from cli_playground.rename_state import migrate
from cli_playground.session_store import SessionStore


def legacy_tree(root: Path) -> Path:
    sessions = SessionStore(root)
    binding = sessions.root / "bindings" / f"{uuid4()}.json"
    binding.write_text(
        json.dumps(
            {
                "id": binding.stem,
                "preview_id": "a" * 32,
                "revision": "b" * 40,
                "credential_hash": "private-hash",
                "sandbox": {
                    "id": str(uuid4()),
                    "state": "terminated",
                    "created_at": "2026-10-19T00:00:00Z",
                    "absolute_deadline": "2026-10-19T01:00:00Z",
                    "reason": "orchestrator_shutdown",
                    "cleanup_pending": False,
                },
            }
        )
    )
    binding.chmod(0o600)
    (root / "preview-bytes").write_bytes(b"immutable preview and artifact identity\x00")
    return binding


def test_copy_converts_bindings_preserves_source_and_retries(tmp_path: Path) -> None:
    source, target = tmp_path / "old", tmp_path / "new"
    binding = legacy_tree(source)
    original = binding.read_bytes()
    migrate(source, target)
    migrated = target / binding.relative_to(source)
    old_data = json.loads(original)
    old_data["sandbox"]["reason"] = "supervisor_shutdown"
    assert json.loads(migrated.read_bytes()) == old_data
    assert migrated.stat().st_mode & 0o777 == 0o600
    assert binding.read_bytes() == original
    assert (target / "preview-bytes").read_bytes() == (source / "preview-bytes").read_bytes()
    assert SessionStore(target).bindings()[0].sandbox.reason == "supervisor_shutdown"
    migrate(source, target)
    (target / "preview-bytes").write_bytes(b"conflict")
    with pytest.raises(ValueError, match="destination"):
        migrate(source, target)
    assert (target / "preview-bytes").read_bytes() == b"conflict"


@pytest.mark.parametrize("pending", ["creates", "uploads", "retirements", "active", "cleanup"])
def test_preconditions_leave_destination_absent(tmp_path: Path, pending: str) -> None:
    source, target = tmp_path / "old", tmp_path / "new"
    binding = legacy_tree(source)
    if pending in {"active", "cleanup"}:
        data = json.loads(binding.read_bytes())
        data["sandbox"]["state"] = "ready" if pending == "active" else "terminated"
        data["sandbox"]["cleanup_pending"] = pending == "cleanup"
        binding.write_text(json.dumps(data))
    else:
        (source / ".operations" / pending / str(uuid4())).write_bytes(b"pending")
    with pytest.raises(ValueError, match="drain"):
        migrate(source, target)
    assert not target.exists()


def test_rejects_symlinks_and_overlapping_roots(tmp_path: Path) -> None:
    source = tmp_path / "old"
    legacy_tree(source)
    with pytest.raises(ValueError, match="separate"):
        migrate(source, source / "new")
    (source / "link").symlink_to(tmp_path)
    with pytest.raises(ValueError, match="regular"):
        migrate(source, tmp_path / "new")


def test_failed_copy_can_retry_without_publishing_partial_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = tmp_path / "old", tmp_path / "new"
    legacy_tree(source)
    with monkeypatch.context() as fault:

        def fail(*args: object, **kwargs: object) -> None:
            raise OSError("disk full")

        fault.setattr("cli_playground.rename_state.shutil.copytree", fail)
        with pytest.raises(OSError, match="disk full"):
            migrate(source, target)
    assert not target.exists()
    migrate(source, target)


def test_killed_converter_releases_lock_for_retry(tmp_path: Path) -> None:
    source, target = tmp_path / "old", tmp_path / "new"
    legacy_tree(source)
    script = """
import sys
from pathlib import Path
import cli_playground.rename_state as migration
def pause(*args, **kwargs):
    print('locked', flush=True)
    sys.stdin.read()
migration.shutil.copytree = pause
migration.migrate(Path(sys.argv[1]), Path(sys.argv[2]))
"""
    with subprocess.Popen(
        [sys.executable, "-c", script, str(source), str(target)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    ) as child:
        try:
            assert child.stdout is not None
            assert child.stdout.readline() == "locked\n"
            with pytest.raises(BlockingIOError):
                migrate(source, target)
        finally:
            child.kill()
            child.wait(timeout=10)
    assert not target.exists()
    migrate(source, target)


@pytest.mark.parametrize("fault", [None, "missing", "lifetime", "active", "duplicate"])
def test_refreshes_attached_binding_only_from_matching_sql_cleanup(
    tmp_path: Path, fault: str | None
) -> None:
    source, target = tmp_path / "old", tmp_path / "new"
    binding = legacy_tree(source)
    data = json.loads(binding.read_bytes())
    view = dict(data["sandbox"])
    receipt = {"view": view, "cleaned_at": "2026-10-19T01:00:01Z"}
    data["sandbox"].update(state="ready", reason=None)
    data["attached"] = True
    binding.write_text(json.dumps(data))
    original = binding.read_bytes()
    if fault == "lifetime":
        view["created_at"] = "2026-10-19T00:01:00Z"
    if fault == "active":
        view["state"] = "ready"
    receipts = tmp_path / "receipts.json"
    receipts.write_text(
        json.dumps([] if fault == "missing" else [receipt] * (2 if fault == "duplicate" else 1))
    )
    if fault:
        with pytest.raises(ValueError):
            migrate(source, target, receipts)
        assert not target.exists()
    else:
        migrate(source, target, receipts)
        migrated = SessionStore(target).bindings()[0]
        assert migrated.sandbox.state == "terminated"
        assert migrated.sandbox.reason == "supervisor_shutdown"
        assert not migrated.attached and not migrated.pending_stop
        assert (target / "preview-bytes").read_bytes() == (source / "preview-bytes").read_bytes()
        migrate(source, target, receipts)
    assert binding.read_bytes() == original


def test_retained_terminal_binding_survives_sql_retention(tmp_path: Path) -> None:
    source, target = tmp_path / "old", tmp_path / "new"
    binding = legacy_tree(source)
    original = binding.read_bytes()
    receipts = tmp_path / "receipts.json"
    receipts.write_text("[]")
    migrate(source, target, receipts)
    assert binding.read_bytes() == original
    assert SessionStore(target).bindings()[0].sandbox.reason == "supervisor_shutdown"
