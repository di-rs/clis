"""Real PostgreSQL COMMIT acknowledgment loss through the owned wire proxy."""

import asyncio
import hashlib
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from boxie.persistence.database import database as session_database
from boxie.persistence.models import Artifact
from boxie.persistence.postgres_artifacts import PostgresArtifactStore
from boxie_test_support.orchestrator import bundle
from database_support import OWNER, database
from sqlalchemy.exc import DBAPIError
from sqlmodel import select

from integration.faults import Faults

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("loss", ["disconnect", "deadline"])
def test_committed_metadata_survives_lost_ack_and_competing_collection(
    postgres_dsn: str, tmp_path: Path, loss: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        if loss == "deadline":
            monkeypatch.setattr("boxie.persistence.postgres_artifacts.OPERATION_SECONDS", 0.3)
        async with database(postgres_dsn, tmp_path) as (_, _, direct_sessions, _):
            info = psycopg.conninfo.conninfo_to_dict(postgres_dsn)
            faults = Faults(tmp_path / "faults", str(info["host"]), int(str(info["port"])))
            server = await asyncio.start_server(faults.postgres, "127.0.0.1", 0)
            info.update(
                host="127.0.0.1", port=str(server.sockets[0].getsockname()[1]), sslmode="disable"
            )
            dsn = psycopg.conninfo.make_conninfo("", **info)
            async with (
                server,
                session_database(dsn) as sessions,
            ):
                store = PostgresArtifactStore(sessions, tmp_path / "bytes")
                identifier, data = uuid4(), bundle({"proof": b"committed"})

                async def chunks() -> Any:
                    yield data

                (faults.root / "hold_commit").touch()
                upload = asyncio.create_task(
                    store.put(OWNER, identifier, hashlib.sha256(data).hexdigest(), chunks())
                )
                collection = None
                try:
                    async with asyncio.timeout(10):
                        while not (faults.root / "commit_seen").exists():
                            await asyncio.sleep(0.01)
                    async with direct_sessions() as session:
                        row = await session.get(Artifact, identifier)
                    assert row is not None
                    path = store.root / str(row.storage_id)
                    assert (path / "files/proof").read_bytes() == b"committed"
                    os.utime(path, (time.time() - 7200,) * 2)
                    collection = asyncio.create_task(store.collect_orphans(minimum_age_seconds=60))
                    await asyncio.sleep(0.05)
                    assert not collection.done() and path.exists()
                    if loss == "disconnect":
                        (faults.root / "hold_commit").unlink()
                        with pytest.raises(DBAPIError):
                            await asyncio.wait_for(upload, 10)
                    else:
                        from boxie.domain.errors import BoxieError

                        with pytest.raises(BoxieError) as expired:
                            await asyncio.wait_for(upload, 10)
                        assert expired.value.status == 503
                        (faults.root / "hold_commit").unlink()
                    if loss == "deadline":
                        with pytest.raises(BoxieError) as waiting:
                            await asyncio.wait_for(collection, 10)
                        assert waiting.value.status == 503
                        assert await store.collect_orphans(minimum_age_seconds=60) == 0
                    else:
                        assert await asyncio.wait_for(collection, 10) == 0
                    restarted = PostgresArtifactStore(direct_sessions, store.root)
                    assert await restarted.collect_orphans(minimum_age_seconds=60) == 0
                    retry = await restarted.put(
                        OWNER, identifier, hashlib.sha256(data).hexdigest(), chunks()
                    )
                    assert not retry.created
                    assert (
                        await restarted.path(OWNER, identifier) / "proof"
                    ).read_bytes() == b"committed"
                finally:
                    (faults.root / "hold_commit").unlink(missing_ok=True)
                    upload.cancel()
                    if collection:
                        collection.cancel()
                    await asyncio.gather(
                        upload, *([collection] if collection else []), return_exceptions=True
                    )

    asyncio.run(exercise())


def test_native_fixture_compose_resolves_owned_fault_boundaries(tmp_path: Path) -> None:
    import json

    from integration.stack import LifecycleStack

    stack = LifecycleStack(tmp_path)
    stack.env["BOXIE_TEST_API_PORT"] = "18890"
    stack.env["BOXIE_TEST_PRODUCT_PORT"] = "18888"
    stack.env["BOXIE_TEST_DATA_MODE"] = "ro"
    config = json.loads(stack.runtime_command("config", "--format", "json"))
    services = config["services"]
    for service, published, target in (("api", "18890", 8090), ("playground", "18888", 8000)):
        assert [
            (port["host_ip"], port["published"], port["target"], port["protocol"])
            for port in services[service]["ports"]
        ] == [("127.0.0.1", published, target, "tcp")]
    for name in ("api", "playground"):
        assert not any("docker.sock" in str(mount) for mount in services[name].get("volumes", []))
    assert services["orchestrator"]["environment"]["DOCKER_HOST"] == "unix:///faults/docker.sock"
    assert not any(
        m["target"] == "/var/run/docker.sock" for m in services["orchestrator"]["volumes"]
    )
    assert any(m["target"] == "/var/run/docker.sock" for m in services["faults"]["volumes"])
    mounts = {m["target"]: m for m in services["orchestrator"]["volumes"]}
    assert mounts[str(tmp_path / "data")]["read_only"] is True
    assert not mounts[str(tmp_path / "data/profiles")].get("read_only", False)
    assert mounts["/fixture/orchestrator_fixture.py"]["read_only"] is True
    assert all(service.get("restart") == "no" for service in services.values())


def test_disconnected_upload_waiter_and_collector_have_one_total_deadline(
    postgres_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from boxie.bootstrap.orchestrator import create_orchestrator_app
    from boxie.domain.errors import BoxieError
    from boxie.persistence.start_guard import PostgresStartGuard
    from boxie.sandbox.execution.orchestrator import Orchestrator
    from boxie.sandbox.transport.client import OrchestratorClient
    from boxie_test_support.api import serve
    from boxie_test_support.execution import Daemon
    from boxie_test_support.orchestrator import Clock

    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (_, store, sessions, config):
            monkeypatch.setattr(
                "boxie.persistence.postgres_artifacts.OPERATION_SECONDS", 0.15, raising=False
            )
            clock = Clock()
            orchestrator = Orchestrator(
                config, Daemon(), store.path, PostgresStartGuard(sessions), clock, clock.monotonic
            )

            app = create_orchestrator_app(
                orchestrator, store, credential=b"orchestrator-test-secret"
            )
            async with serve(app) as origin:
                await store._files_lock.acquire()
                try:
                    async with OrchestratorClient(
                        origin, b"orchestrator-test-secret", timeout=0.05
                    ) as upstream:

                        async def chunks() -> Any:
                            yield bundle()

                        with pytest.raises(BoxieError) as error:
                            await upstream.put(
                                OWNER, uuid4(), hashlib.sha256(bundle()).hexdigest(), chunks()
                            )
                        assert error.value.status == 503
                    # Upstream already disconnected; the orchestrator's own deadline must
                    # evict its waiter without needing to read another body byte.
                    await asyncio.sleep(0.2)
                    assert not store._files_lock._waiters
                    with pytest.raises(BoxieError) as timeout:
                        await asyncio.wait_for(store.collect(), 0.4)
                    assert timeout.value.status == 503
                finally:
                    store._files_lock.release()
                assert await store.collect() == 0
                assert not list(store.root.glob(".upload-*"))

    asyncio.run(exercise())


def test_cancelled_invalid_and_oversized_uploads_never_publish(
    postgres_dsn: str, tmp_path: Path
) -> None:
    from boxie.domain.errors import BoxieError
    from boxie.persistence.artifacts import ArtifactLimits

    async def exercise() -> None:
        async with database(postgres_dsn, tmp_path) as (_, store, sessions, _):

            async def interrupted() -> Any:
                yield b"first chunk"
                raise asyncio.CancelledError

            with pytest.raises(asyncio.CancelledError):
                await store.put(OWNER, uuid4(), "0" * 64, interrupted())
            data = bundle()

            async def chunks() -> Any:
                yield data

            with pytest.raises(BoxieError) as mismatch:
                await store.put(OWNER, uuid4(), "0" * 64, chunks())
            assert mismatch.value.status == 422
            limited = PostgresArtifactStore(
                sessions, store.root, ArtifactLimits(compressed_bytes=10)
            )
            with pytest.raises(BoxieError) as oversized:
                await limited.put(OWNER, uuid4(), hashlib.sha256(data).hexdigest(), chunks())
            assert oversized.value.status == 413
            assert not list(store.root.iterdir())
            async with sessions() as session:
                assert (await session.exec(select(Artifact))).all() == []

    asyncio.run(exercise())
