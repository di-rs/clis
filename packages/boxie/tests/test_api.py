import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx
import pytest
from boxie.api.app import ApiConfig, create_app
from boxie.sandbox.transport.client import SupervisorClient
from boxie_test_support.api import CONSUMER_AUTH, FOREIGN_AUTH, artifact, create, ready, serve
from boxie_test_support.supervisor import bundle
from database_support import counts, database

pytestmark = pytest.mark.integration


@asynccontextmanager
async def stack(dsn: str, root: Path) -> AsyncIterator[tuple[httpx.AsyncClient, Any]]:
    async with database(dsn, root) as (life, artifacts, pool, _):
        async with SupervisorClient("http://127.0.0.1:1", b"supervisor-test-secret") as terminal:
            app = create_app(
                ApiConfig(
                    release="test",
                    consumers={"one": b"consumer-one-secret", "two": b"consumer-two-secret"},
                ),
                life,
                artifacts,
                terminal,
            )
            async with serve(app) as url:
                async with httpx.AsyncClient(base_url=url, headers=CONSUMER_AUTH) as client:
                    yield client, pool


def test_api_auth_ownership_idempotency_and_validation(tmp_path: Path, postgres_dsn: str) -> None:
    async def exercise() -> None:
        async with stack(postgres_dsn, tmp_path) as (client, pool):
            unauthorized = await client.get(
                "/v1/sandboxes/" + str(uuid4()), headers={"Authorization": ""}
            )
            assert unauthorized.status_code == 401
            artifact_id = await artifact(client)
            spec = {"artifact_id": artifact_id}
            responses = await asyncio.gather(
                *(
                    client.post("/v1/sandboxes", json=spec, headers={"Idempotency-Key": "first"})
                    for _ in range(4)
                )
            )
            assert all(response.status_code == 202 for response in responses)
            identifier = responses[0].json()["id"]
            assert {response.json()["id"] for response in responses} == {identifier}
            await ready(client, identifier, "accepted")
            assert (await counts(pool))[:3] == (1, 1, 1)
            foreign = await client.get(f"/v1/sandboxes/{identifier}", headers=FOREIGN_AUTH)
            assert foreign.status_code == 404
            assert (
                await client.delete(f"/v1/sandboxes/{identifier}", headers=FOREIGN_AUTH)
            ).status_code == 404
            assert (
                await client.post(
                    "/v1/sandboxes", json=spec, headers={**FOREIGN_AUTH, "Idempotency-Key": "first"}
                )
            ).status_code == 404
            conflict = await client.post(
                "/v1/sandboxes",
                json={"artifact_id": str(uuid4())},
                headers={"Idempotency-Key": "first"},
            )
            assert conflict.status_code == 409
            overrides: tuple[dict[str, object], ...] = (
                {"profile_id": "default"},
                {"image": "arbitrary"},
                {"host_path": "/etc"},
                {"command": ["/opt/boxie/shell.sh"]},
                {"env": {"TERM": "xterm-256color"}},
                {"consumer_id": "two"},
                {"policy": {}},
            )
            for patch in overrides:
                assert (
                    await client.post(
                        "/v1/sandboxes", json={**spec, **patch}, headers={"Idempotency-Key": "new"}
                    )
                ).status_code == 422
            assert (await client.post("/v1/sandboxes", json=spec)).status_code == 422
            stopped = await client.delete(f"/v1/sandboxes/{identifier}")
            assert stopped.status_code == 200
            await ready(client, identifier, "terminating")
            assert (await counts(pool))[:3] == (1, 1, 1)

    asyncio.run(exercise())


def test_immutable_artifact_status_and_retirement(tmp_path: Path, postgres_dsn: str) -> None:
    async def exercise() -> None:
        async with stack(postgres_dsn, tmp_path) as (client, _pool):
            data = bundle()
            identifier = str(uuid4())
            initial = await client.put(
                f"/v1/artifacts/{identifier}",
                content=data,
                headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest()},
            )
            assert initial.status_code == 201
            retry = await client.put(
                f"/v1/artifacts/{identifier}",
                content=data,
                headers={"X-Content-SHA256": hashlib.sha256(data).hexdigest()},
            )
            assert retry.status_code == 200
            conflict = await client.put(
                f"/v1/artifacts/{identifier}",
                content=bundle({"different": b"bytes"}),
                headers={
                    "X-Content-SHA256": hashlib.sha256(bundle({"different": b"bytes"})).hexdigest()
                },
            )
            assert conflict.status_code == 409
            assert (
                await client.delete(f"/v1/artifacts/{identifier}", headers=FOREIGN_AUTH)
            ).status_code == 404
            assert (await client.delete(f"/v1/artifacts/{identifier}")).status_code == 204
            assert (await client.delete(f"/v1/artifacts/{identifier}")).status_code == 204
            assert (
                await client.post(
                    "/v1/sandboxes",
                    json={"artifact_id": identifier},
                    headers={"Idempotency-Key": "retired"},
                )
            ).status_code == 409

    asyncio.run(exercise())


def test_sql_owns_capacity(tmp_path: Path, postgres_dsn: str) -> None:
    async def exercise() -> None:
        async with stack(postgres_dsn, tmp_path) as (client, _pool):
            identifier = await artifact(client)
            for index in range(8):
                await create(client, identifier, str(index))
            assert (
                await client.post(
                    "/v1/sandboxes",
                    json={"artifact_id": identifier},
                    headers={"Idempotency-Key": "second"},
                )
            ).status_code == 429

    asyncio.run(exercise())
