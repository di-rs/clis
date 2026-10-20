"""Legacy state conversion retains identities and fails without publishing partial data."""

import json
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from boxie.persistence.rename_state import convert_journal
from boxie.sandbox.execution.journal import JournalData

FIXTURE = Path(__file__).parent / "inputs/legacy-execution-journal.json"


def test_journal_command_preserves_ownership_snapshot_and_retry(tmp_path: Path) -> None:
    source, destination = tmp_path / "old.json", tmp_path / "new.json"
    source.write_bytes(FIXTURE.read_bytes())
    source.chmod(0o600)
    command = [
        sys.executable,
        "-m",
        "boxie.persistence.rename_state",
        str(source),
        str(destination),
    ]
    subprocess.run(command, check=True, capture_output=True)
    converted = destination.read_bytes()
    subprocess.run(command, check=True, capture_output=True)
    assert source.read_bytes() == FIXTURE.read_bytes()
    assert destination.read_bytes() == converted
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600
    legacy = json.loads(source.read_bytes())["records"][0]
    record = JournalData.model_validate_json(converted).records[0]
    assert record.consumer_id == record.spec.profile_id == "cli-playground"
    assert record.snapshot.profile_id == "playground"
    assert record.reason == "supervisor_restart"
    assert record.spec.command == ("/opt/cli-playground/shell.sh",)
    assert record.snapshot.command == tuple(legacy["snapshot"]["command"])
    assert record.snapshot.config_digest == legacy["snapshot"]["config_digest"]
    assert record.snapshot.image_digest == legacy["snapshot"]["image_digest"]
    assert str(record.id) == legacy["id"]
    assert str(record.spec.artifact_id) == legacy["spec"]["artifact_id"]
    assert record.identity.model_dump(mode="json") == legacy["identity"]
    assert str(record.boot_id) == legacy["boot_id"]
    assert (destination.stat().st_uid, destination.stat().st_gid) == (
        source.stat().st_uid,
        source.stat().st_gid,
    )
    destination.chmod(0o644)
    with pytest.raises(FileExistsError, match="conflicts"):
        convert_journal(source, destination)


@pytest.mark.parametrize(
    "change",
    [
        {"state": "ready"},
        {"cleanup_pending": True},
        {"removal_confirmed": False},
        {"creation_uncertain": True},
        {"start_uncertain": True},
        {"attached": True},
    ],
)
def test_journal_rejects_live_or_uncertain_state(tmp_path: Path, change: dict[str, Any]) -> None:
    source, destination = tmp_path / "old.json", tmp_path / "new.json"
    data = json.loads(FIXTURE.read_bytes())
    data["records"][0].update(change)
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        convert_journal(source, destination)
    assert not destination.exists()


def test_journal_rejects_conflicting_destination_and_duplicate_identity(tmp_path: Path) -> None:
    source, destination = tmp_path / "old.json", tmp_path / "new.json"
    source.write_bytes(FIXTURE.read_bytes())
    destination.write_bytes(b"retained operator data")
    with pytest.raises(FileExistsError):
        convert_journal(source, destination)
    assert destination.read_bytes() == b"retained operator data"
    destination.unlink()
    data = json.loads(FIXTURE.read_bytes())
    data["records"].append(data["records"][0] | {"consumer_id": "cli-playground"})
    source.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="duplicate"):
        convert_journal(source, destination)
    assert not destination.exists()


def test_journal_failed_publication_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import boxie.persistence.rename_state as conversion

    source, destination = tmp_path / "old.json", tmp_path / "new.json"
    source.write_bytes(FIXTURE.read_bytes())
    with monkeypatch.context() as patch:

        def interrupted(*args: object) -> None:
            raise OSError("interrupted after file publication")

        patch.setattr(conversion, "sync_directory", interrupted)
        with pytest.raises(OSError):
            convert_journal(source, destination)
    convert_journal(source, destination)
    assert source.read_bytes() == FIXTURE.read_bytes()
    assert JournalData.model_validate_json(destination.read_bytes())


@pytest.mark.integration
@pytest.mark.parametrize("blocked", [None, "active", "conflict"])
def test_sql_rename_preserves_retry_artifact_and_snapshot(
    postgres_dsn: str, blocked: str | None, tmp_path: Path
) -> None:
    import hashlib

    import psycopg
    from alembic import command
    from boxie.persistence.migrate import migration_config
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from sqlalchemy.exc import DBAPIError

    receipts_sql = (
        Path(__file__).parents[3] / "infra/environments/single-host/cleanup-receipts.sql"
    ).read_text()

    def export_receipts() -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        with psycopg.connect(postgres_dsn, autocommit=True) as receipts_connection:
            with receipts_connection.execute(receipts_sql) as cursor:
                while True:
                    if cursor.description is not None:
                        result = cursor.fetchone()
                        assert result is not None
                        results.extend(result[0])
                    if not cursor.nextset():
                        break
        return results

    config = migration_config(postgres_dsn)
    command.upgrade(config, "0004_observability_cleanup")
    legacy = json.loads(FIXTURE.read_bytes())["records"][0]
    with psycopg.connect(postgres_dsn) as connection:
        connection.execute(
            "INSERT INTO artifacts (id, consumer_id, digest, storage_id) VALUES (%s,%s,%s,%s)",
            (legacy["spec"]["artifact_id"], "playground", "a" * 64, legacy["spec"]["artifact_id"]),
        )
        connection.execute(
            """INSERT INTO sandboxes
               (id, consumer_id, idempotency_key, request_hash, spec, snapshot, installation_id,
                host_id, orchestrator_boot_id, accepted_at, absolute_deadline, reason, state, cleaned_at)
               VALUES (%s,'playground','retry-key','legacy-hash',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                legacy["id"],
                Jsonb(legacy["spec"]),
                Jsonb(legacy["snapshot"]),
                legacy["identity"]["installation_id"],
                legacy["identity"]["host_id"],
                legacy["boot_id"],
                legacy["accepted_at"],
                legacy["absolute_deadline"],
                legacy["reason"],
                "ready" if blocked == "active" else "terminated",
                None if blocked == "active" else legacy["absolute_deadline"],
            ),
        )
        connection.execute(
            """INSERT INTO orchestrator_observations
               (sandbox_id, generation, boot_id, event_version, observed_at, resource_present,
                removal_confirmed) VALUES (%s,1,%s,7,%s,false,true)""",
            (legacy["id"], legacy["boot_id"], legacy["absolute_deadline"]),
        )
        if blocked == "conflict":
            connection.execute(
                "INSERT INTO artifacts (id, consumer_id, digest, storage_id) VALUES (%s,%s,%s,%s)",
                (legacy["boot_id"], "cli-playground", "b" * 64, legacy["boot_id"]),
            )
    if blocked == "active":
        exported = None
        with pytest.raises(psycopg.errors.RaiseException, match="drain sandboxes"):
            exported = export_receipts()
        assert exported is None
    if blocked:
        with pytest.raises(DBAPIError, match="drain|conflicting"):
            command.upgrade(config, "head")
        with psycopg.connect(postgres_dsn) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "0004_observability_cleanup",
            )
            assert connection.execute("SELECT consumer_id FROM sandboxes").fetchone() == (
                "playground",
            )
            if blocked == "active":
                connection.execute(
                    "UPDATE sandboxes SET state='terminated', cleaned_at=absolute_deadline"
                )
            else:
                connection.execute("DELETE FROM artifacts WHERE consumer_id='cli-playground'")
    receipts_before = export_receipts()
    assert len(receipts_before) == 1
    receipt = receipts_before[0]
    assert receipt["view"] == {
        "id": legacy["id"],
        "state": "terminated",
        "created_at": "2026-10-18T12:00:00+00:00",
        "absolute_deadline": "2026-10-18T13:00:00+00:00",
        "reason": "orchestrator_restart",
        "cleanup_pending": False,
    }
    assert receipt["cleaned_at"] == "2026-10-18T13:00:00+00:00"
    command.upgrade(config, "head")
    command.upgrade(config, "head")
    assert export_receipts() == [
        receipt | {"view": receipt["view"] | {"reason": "supervisor_restart"}}
    ]
    command.check(config)
    with psycopg.connect(postgres_dsn, row_factory=dict_row) as query:
        row = query.execute("SELECT * FROM sandboxes").fetchone()
        assert row is not None
        assert row["consumer_id"] == row["spec"]["profile_id"] == "cli-playground"
        assert row["idempotency_key"] == "retry-key"
        assert (
            row["request_hash"]
            == hashlib.sha256(
                json.dumps(row["spec"], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        assert row["reason"] == "supervisor_restart"
        assert row["spec"]["command"] == ["/opt/cli-playground/shell.sh"]
        assert row["snapshot"] == legacy["snapshot"]
        assert str(row["id"]) == legacy["id"]
        assert str(row["supervisor_boot_id"]) == legacy["boot_id"]
        assert str(row["installation_id"]) == legacy["identity"]["installation_id"]
        assert row["host_id"] == legacy["identity"]["host_id"]
        artifact = query.execute("SELECT * FROM artifacts").fetchone()
        assert artifact is not None
        assert artifact["consumer_id"] == "cli-playground"
        assert str(artifact["id"]) == str(artifact["storage_id"]) == legacy["spec"]["artifact_id"]
        observation = query.execute("SELECT * FROM supervisor_observations").fetchone()
        assert observation is not None
        assert str(observation["sandbox_id"]) == legacy["id"]
        assert observation["event_version"] == 7
        constraints = query.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'supervisor_observations'::regclass"
        ).fetchall()
        assert {constraint["conname"] for constraint in constraints} == {
            "supervisor_observations_pkey",
            "supervisor_observations_sandbox_id_fkey",
            "observation_removal",
            "observation_version",
        }

    import asyncio

    from boxie.persistence.database import database
    from boxie.persistence.start_guard import PostgresStartGuard

    source, destination = tmp_path / "old.json", tmp_path / "new.json"
    source.write_bytes(FIXTURE.read_bytes())
    convert_journal(source, destination)
    record = JournalData.model_validate_json(destination.read_bytes()).records[0]

    async def confirm_terminal_receipt() -> None:
        async with database(postgres_dsn) as sessions:
            assert await PostgresStartGuard(sessions).terminal_receipt(record.request())

    asyncio.run(confirm_terminal_receipt())


@pytest.mark.parametrize(
    "legacy, canonical",
    [
        ("orchestrator_restart", "supervisor_restart"),
        ("orchestrator_shutdown", "supervisor_shutdown"),
    ],
)
def test_recorded_stop_activity_accepts_legacy_reason(legacy: str, canonical: str) -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import cast
    from unittest.mock import AsyncMock
    from uuid import UUID

    from boxie.domain.models import Principal, TerminationReason
    from boxie.persistence.postgres import PostgresLifecycle
    from boxie.sandbox.execution.supervisor import Supervisor
    from boxie.temporal.activities import LifecycleActivities
    from boxie.temporal.contracts import StopRequest

    identifier = UUID("00000000-0000-0000-0000-000000000002")
    principal = Principal("cli-playground")
    terminate = AsyncMock()
    repository = cast(
        PostgresLifecycle,
        SimpleNamespace(
            load_request=AsyncMock(
                return_value=SimpleNamespace(principal=principal, sandbox_id=identifier)
            ),
            terminate=terminate,
        ),
    )
    activities = LifecycleActivities(repository, cast(Supervisor, None))
    asyncio.run(activities.request_stop(StopRequest(sandbox_id=str(identifier), reason=legacy)))
    terminate.assert_awaited_once_with(principal, identifier, TerminationReason(canonical))


def test_converted_journal_validates_immutable_historical_profile(tmp_path: Path) -> None:
    import hashlib

    from boxie.sandbox.profiles import ProfileRegistry

    old = json.loads(FIXTURE.read_bytes())
    snapshot = old["records"][0]["snapshot"]
    profile = {
        "image_digest": snapshot["image_digest"],
        "commands": [snapshot["command"]],
        "env": {key: [value] for key, value in snapshot["env"].items()},
    }
    approved = json.dumps({"playground": profile}).encode()
    digest = hashlib.sha256(approved).hexdigest()
    snapshot["config_digest"] = digest
    versions = tmp_path / "profiles"
    versions.mkdir()
    (versions / f"{digest}.json").write_bytes(approved)
    current = tmp_path / "current.json"
    current.write_text(
        json.dumps({"cli-playground": profile | {"commands": [["/opt/cli-playground/shell.sh"]]}})
    )
    source, destination = tmp_path / "old.json", tmp_path / "new.json"
    source.write_text(json.dumps(old))
    convert_journal(source, destination)
    record = JournalData.model_validate_json(destination.read_bytes()).records[0]
    registry = ProfileRegistry(current, versions)
    assert registry.validate_snapshot(record.snapshot.domain()) == record.snapshot.domain()
    assert record.snapshot.model_dump(mode="json") == snapshot
    assert (versions / f"{digest}.json").read_bytes() == approved
