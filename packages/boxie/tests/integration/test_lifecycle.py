"""Mandatory Linux amd64/runsc lifecycle proof; no substituted service boundaries."""

import asyncio
import hashlib
import json
import os
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from boxie.persistence.models import (
    Artifact,
    CapacitySlot,
    Sandbox,
    SandboxArtifactRef,
    SupervisorObservationRecord,
)
from boxie.temporal.workflows import SandboxLifecycle
from boxie_test_support.supervisor import bundle
from sqlalchemy import and_, func
from sqlmodel import col, select
from temporal_support import tls
from temporalio.client import Client
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.worker import Replayer

from integration.stack import LifecycleStack

pytestmark = pytest.mark.integration


def assert_journal_failure(logs: str) -> None:
    events = [json.loads(line) for line in logs.splitlines() if line]
    assert any(
        event.get("schema_version") == 1
        and event.get("service") == "boxie"
        and event.get("role") == "supervisor"
        and event.get("level") == "ERROR"
        and event.get("event") == "journal_failed"
        for event in events
    ), "structured supervisor journal failure event missing"


@pytest.fixture
def lifecycle_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[LifecycleStack]:
    stack = LifecycleStack(tmp_path_factory.mktemp("boxie-native-lifecycle"))
    try:
        stack.start()
        yield stack
    finally:
        stack.close()


async def until(client: httpx.AsyncClient, identifier: str, state: str) -> dict[str, Any]:
    async with asyncio.timeout(90):
        while True:
            response = await client.get(f"/v1/sandboxes/{identifier}")
            response.raise_for_status()
            item: dict[str, Any] = response.json()
            if item["state"] == state and (state != "terminated" or not item["cleanup_pending"]):
                return item
            await asyncio.sleep(0.2)


async def publish(client: httpx.AsyncClient) -> str:
    identifier, data = str(uuid4()), bundle({"message.txt": b"native artifact bytes\n"})
    result = await client.put(
        f"/v1/artifacts/{identifier}",
        content=data,
        headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest()},
    )
    assert result.status_code == 201, result.text
    return identifier


async def create(client: httpx.AsyncClient, artifact: str, key: str) -> str:
    response = await client.post(
        "/v1/sandboxes",
        headers={"Idempotency-Key": key},
        json={
            "artifact_id": artifact,
            "profile_id": "smoke",
            "command": ["/bin/sh"],
            "env": {},
            "policy": {"max_seconds": 3600, "idle_seconds": 600, "disconnect_seconds": 120},
        },
    )
    assert response.status_code == 202, response.text
    return str(response.json()["id"])


async def accounting(stack: LifecycleStack, identifier: str, count: int) -> None:
    sid = UUID(identifier)
    rows = await stack.rows(
        select(
            select(func.count())
            .select_from(CapacitySlot)
            .where(col(CapacitySlot.sandbox_id) == sid)
            .scalar_subquery()
            .label("slots"),
            select(func.count())
            .select_from(SandboxArtifactRef)
            .where(col(SandboxArtifactRef.sandbox_id) == sid)
            .scalar_subquery()
            .label("refs"),
        )
    )
    assert rows == [{"slots": count, "refs": count}]


async def pending_after_restart(
    stack: LifecycleStack, client: httpx.AsyncClient, identifier: str
) -> None:
    boot = await asyncio.to_thread(stack.supervisor_boot)
    # Failed journal saves deliberately suppress push reports. The workflow's
    # 30s authenticated inspection (or cleanup retry) binds the new boot and
    # repairs SQL; startup or a fixed 3s delay is not a projection barrier.
    # Cleanup may bind then fail its RPC before an observation can be saved.
    # Its committed SQL stop remains authoritative even with no observation row.
    async with asyncio.timeout(90):
        while True:
            await accounting(stack, identifier, 1)
            rows = await stack.rows(
                select(
                    Sandbox.supervisor_boot_id,
                    Sandbox.state,
                    Sandbox.cleanup_pending,
                    SupervisorObservationRecord.removal_confirmed,
                )
                .outerjoin(
                    SupervisorObservationRecord,
                    and_(
                        col(SupervisorObservationRecord.sandbox_id) == col(Sandbox.id),
                        col(SupervisorObservationRecord.boot_id) == col(Sandbox.supervisor_boot_id),
                    ),
                )
                .where(col(Sandbox.id) == UUID(identifier))
            )
            if rows and rows[0]["supervisor_boot_id"] == boot:
                assert not rows[0]["removal_confirmed"]
                if rows[0]["state"] != "terminating" or not rows[0]["cleanup_pending"]:
                    await asyncio.sleep(0.2)
                    continue
                result = (await client.get(f"/v1/sandboxes/{identifier}")).json()
                assert result["cleanup_pending"] and result["state"] == "terminating"
                return
            await asyncio.sleep(0.2)


def test_process_loss_failed_delete_and_history_replay(lifecycle_stack: LifecycleStack) -> None:
    async def exercise() -> None:
        stack = lifecycle_stack
        temporal = await Client.connect(
            stack.address,
            namespace="boxie",
            tls=tls(stack, "operator"),
            data_converter=pydantic_data_converter,
        )
        async with httpx.AsyncClient(
            base_url=stack.api_url,
            headers={"Authorization": "Bearer " + stack.consumer},
            timeout=30,
        ) as client:
            artifact = await publish(client)
            for service in ("api", "temporal", "supervisor"):
                key = uuid4().hex
                identifier = await create(client, artifact, key)
                original = await until(client, identifier, "ready")
                await accounting(stack, identifier, 1)
                assert stack.children()
                stack.fault("fail_delete", True)
                await asyncio.to_thread(stack.process, service, "kill")
                if service == "api":
                    await asyncio.to_thread(stack.process, service, "start")
                    await asyncio.to_thread(stack.wait_ready)
                    assert await create(client, artifact, key) == identifier
                stop = await client.delete(f"/v1/sandboxes/{identifier}")
                assert stop.status_code == 200
                current = (await client.get(f"/v1/sandboxes/{identifier}")).json()
                assert (
                    current["cleanup_pending"]
                    and current["absolute_deadline"] == original["absolute_deadline"]
                )
                await accounting(stack, identifier, 1)
                if service != "api":
                    await asyncio.to_thread(stack.process, service, "start")
                async with asyncio.timeout(60):
                    while not (stack.root / "faults/delete_seen").exists():
                        await asyncio.sleep(0.1)
                assert stack.children()
                await accounting(stack, identifier, 1)
                stack.fault("fail_delete", False)
                (stack.root / "faults/delete_seen").unlink(missing_ok=True)
                await until(client, identifier, "terminated")
                await accounting(stack, identifier, 0)
                assert not stack.children()
                handle = temporal.get_workflow_handle("boxie/sandbox/" + identifier)
                await asyncio.wait_for(handle.result(), 60)
                history = await handle.fetch_history()
                (stack.root / f"history-{service}.json").write_text(history.to_json())
                await Replayer(
                    workflows=[SandboxLifecycle], data_converter=pydantic_data_converter
                ).replay_workflow(history)
                await asyncio.to_thread(stack.wait_ready)
            await client.delete(f"/v1/artifacts/{artifact}")

    asyncio.run(exercise())


def test_successful_artifact_commit_lost_ack_collector_overlap_and_supervisor_restart(
    lifecycle_stack: LifecycleStack,
) -> None:
    async def exercise() -> None:
        stack = lifecycle_stack
        artifact, data = (
            str(uuid4()),
            bundle({"proof.txt": b"committed bytes survive lost acknowledgment\n"}),
        )
        async with httpx.AsyncClient(
            base_url=stack.api_url,
            headers={"Authorization": "Bearer " + stack.consumer},
            timeout=45,
        ) as client:
            (stack.root / "faults/commit_seen").unlink(missing_ok=True)
            entered = stack.root / "faults/collection_entered"
            async with asyncio.timeout(40):
                while not entered.exists():
                    await asyncio.sleep(0.05)
            previous_collection = entered.read_text()
            # Start five seconds before the unchanged 30s production collector tick.
            delay = max(0, 25 - (time.time() - int(previous_collection) / 1e9))
            await asyncio.sleep(delay)
            stack.fault("hold_commit", True)
            upload = asyncio.create_task(
                client.put(
                    f"/v1/artifacts/{artifact}",
                    content=data,
                    headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest()},
                )
            )
            try:
                async with asyncio.timeout(20):
                    while not (stack.root / "faults/commit_seen").exists():
                        await asyncio.sleep(0.05)
                row = (
                    await stack.rows(
                        select(Artifact.storage_id, Artifact.digest).where(
                            col(Artifact.id) == UUID(artifact)
                        )
                    )
                )[0]
                storage = stack.root / "data/artifacts" / str(row["storage_id"])
                assert (
                    storage / "files/proof.txt"
                ).read_bytes() == b"committed bytes survive lost acknowledgment\n"
                assert row["digest"] == hashlib.sha256(data).hexdigest()
                os.utime(storage, (time.time() - 7200,) * 2)
                async with asyncio.timeout(15):
                    while entered.read_text() == previous_collection:
                        await asyncio.sleep(0.05)
                attempted = entered.read_text()
                completed = stack.root / "faults/collection_completed"
                assert completed.read_text() != attempted
                assert not upload.done() and storage.exists()
                # Entry without completion proves the real collector waits behind
                # the publication still awaiting its successful COMMIT response.
                await asyncio.to_thread(stack.process, "supervisor", "kill")
                stack.fault("hold_commit", False)
                await asyncio.to_thread(stack.process, "supervisor", "start")
                await asyncio.to_thread(stack.wait_ready)
                response = await upload
                assert response.status_code == 503
                retry = await client.put(
                    f"/v1/artifacts/{artifact}",
                    content=data,
                    headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest()},
                )
                assert retry.status_code == 200
                await asyncio.sleep(31)
                assert (
                    storage / "files/proof.txt"
                ).read_bytes() == b"committed bytes survive lost acknowledgment\n"
                identifier = await create(client, artifact, uuid4().hex)
                await until(client, identifier, "ready")
                await accounting(stack, identifier, 1)
                await client.delete(f"/v1/artifacts/{artifact}")
                assert storage.exists()
                await client.delete(f"/v1/sandboxes/{identifier}")
                await until(client, identifier, "terminated")
                await accounting(stack, identifier, 0)
                async with asyncio.timeout(40):
                    while storage.exists():
                        await asyncio.sleep(0.2)
            finally:
                stack.fault("hold_commit", False)
                upload.cancel()
                await asyncio.gather(upload, return_exceptions=True)

    asyncio.run(exercise())


@pytest.mark.parametrize("unknown_create", [False, True])
def test_sustained_journal_failure_across_supervisor_restart(
    lifecycle_stack: LifecycleStack,
    unknown_create: bool,
) -> None:
    async def exercise() -> None:
        stack = lifecycle_stack
        async with httpx.AsyncClient(
            base_url=stack.api_url,
            headers={"Authorization": "Bearer " + stack.consumer},
            timeout=30,
        ) as client:
            artifact = await publish(client)
            stack.fault("hold_create", unknown_create)
            (stack.root / "faults/create_seen").unlink(missing_ok=True)
            identifier = await create(client, artifact, uuid4().hex)
            if unknown_create:
                async with asyncio.timeout(40):
                    while not (stack.root / "faults/create_seen").exists():
                        await asyncio.sleep(0.1)
            else:
                await until(client, identifier, "ready")
            await accounting(stack, identifier, 1)
            assert stack.children()
            await asyncio.to_thread(stack.process, "supervisor", "kill")
            stack.env["BOXIE_TEST_DATA_MODE"] = "ro"
            stack.fault("hold_create", False)
            await asyncio.to_thread(
                stack.runtime_command,
                "up",
                "--detach",
                "--no-deps",
                "--force-recreate",
                "supervisor",
            )
            async with asyncio.timeout(40):
                while stack.children():
                    await asyncio.sleep(0.2)
            # Profiles alone remain writable for bootstrap; execution.json and its
            # parent stay read-only. Physical deletion proves startup reached cleanup.
            logs = await asyncio.to_thread(
                stack.runtime_command, "logs", "--no-color", "--no-log-prefix", "supervisor"
            )
            assert_journal_failure(logs)
            await asyncio.to_thread(stack.process, "supervisor", "kill")
            await asyncio.to_thread(stack.process, "supervisor", "start")
            await pending_after_restart(stack, client, identifier)
            assert (await client.get("/readyz")).status_code == 503
            stack.env["BOXIE_TEST_DATA_MODE"] = "rw"
            await asyncio.to_thread(
                stack.runtime_command,
                "up",
                "--detach",
                "--no-deps",
                "--force-recreate",
                "supervisor",
            )
            if unknown_create:
                # Discovery/deletion during a failed save was never saved to the journal. A new
                # boot sees unknown create + absence and must retain the reservation.
                await pending_after_restart(stack, client, identifier)
                await accounting(stack, identifier, 1)
                assert not stack.children()
                # Only fixture teardown disposes of this entire owned environment.
            else:
                await until(client, identifier, "terminated")
                await accounting(stack, identifier, 0)
                assert not stack.children()
                await asyncio.to_thread(stack.wait_ready)
                await client.delete(f"/v1/artifacts/{artifact}")

    asyncio.run(exercise())


def test_unknown_create_ack_loss_recovers_with_persisted_discovery(
    lifecycle_stack: LifecycleStack,
) -> None:
    async def exercise() -> None:
        stack = lifecycle_stack
        async with httpx.AsyncClient(
            base_url=stack.api_url,
            headers={"Authorization": "Bearer " + stack.consumer},
            timeout=30,
        ) as client:
            artifact = await publish(client)
            stack.fault("hold_create", True)
            identifier = await create(client, artifact, uuid4().hex)
            async with asyncio.timeout(40):
                while not (stack.root / "faults/create_seen").exists():
                    await asyncio.sleep(0.1)
            await accounting(stack, identifier, 1)
            assert stack.children()
            await asyncio.to_thread(stack.process, "supervisor", "kill")
            stack.fault("hold_create", False)
            await asyncio.to_thread(stack.process, "supervisor", "start")
            await until(client, identifier, "terminated")
            await accounting(stack, identifier, 0)
            assert not stack.children()
            await client.delete(f"/v1/artifacts/{artifact}")

    asyncio.run(exercise())


def test_native_generic_product_and_browser(lifecycle_stack: LifecycleStack) -> None:
    stack = lifecycle_stack
    for module, extra in (
        ("boxie_infra.smoke", []),
        ("cli_playground_infra.smoke", ["--binary", str(stack.binary)]),
    ):
        subprocess.run(
            [
                "uv",
                "run",
                "--locked",
                "python",
                "-m",
                module,
                "--boxie-url",
                stack.api_url,
                "--credential-file",
                str(stack.root / "consumer.secret"),
                *extra,
            ],
            check=True,
            timeout=180,
        )
    if os.environ.get("BOXIE_BROWSER") == "1":
        subprocess.run(
            ["pnpm", "exec", "playwright", "test", "--config", "playwright.native.config.ts"],
            cwd=Path(__file__).parents[4] / "tools/cli-playground/frontend",
            env={
                **os.environ,
                "BOXIE_NATIVE_ORIGIN": stack.product_url,
                "BOXIE_NATIVE_BINARY": str(stack.binary),
                "BOXIE_NATIVE_DEPLOY_TOKEN": stack.deploy,
            },
            check=True,
            timeout=180,
        )
    for _ in range(120):
        if not stack.children():
            break
        time.sleep(0.5)
    assert not stack.children()
