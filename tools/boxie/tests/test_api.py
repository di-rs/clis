import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

from boxie_test_support.api import FOREIGN_AUTH, artifact, create, ready, stack
from boxie_test_support.orchestrator import bundle, request


def test_api_auth_ownership_idempotency_and_validation(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with stack(tmp_path) as (client, orchestrator, daemon, _clock):
            unauthorized = await client.get(
                "/v1/sandboxes/" + str(uuid4()), headers={"Authorization": ""}
            )
            assert unauthorized.status_code == 401
            artifact_id = await artifact(client)
            spec = request(artifact_id)["spec"]
            responses = await asyncio.gather(
                *(
                    client.post("/v1/sandboxes", json=spec, headers={"Idempotency-Key": "first"})
                    for _ in range(4)
                )
            )
            assert all(response.status_code == 202 for response in responses)
            identifier = responses[0].json()["id"]
            assert {response.json()["id"] for response in responses} == {identifier}
            await ready(client, identifier)
            assert orchestrator.reserved_count == 1 and len(daemon.configs) == 1
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
                "/v1/sandboxes", json={**spec, "env": {}}, headers={"Idempotency-Key": "first"}
            )
            assert conflict.status_code == 409
            for patch in (
                {"profile_id": "unknown"},
                {"image": "arbitrary"},
                {"consumer_id": "two"},
                {"policy": {"max_seconds": 3601}},
            ):
                assert (
                    await client.post(
                        "/v1/sandboxes", json={**spec, **patch}, headers={"Idempotency-Key": "new"}
                    )
                ).status_code == 422
            assert (await client.post("/v1/sandboxes", json=spec)).status_code == 422
            stopped = await client.delete(f"/v1/sandboxes/{identifier}")
            assert stopped.status_code == 200
            await ready(client, identifier, "terminated")
            assert not daemon.configs and orchestrator.reserved_count == 0

    asyncio.run(exercise())


def test_immutable_artifact_status_and_retirement(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with stack(tmp_path) as (client, _orchestrator, _daemon, _clock):
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
                content=b"bad",
                headers={"X-Content-SHA256": "a" * 64},
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
                    json=request(identifier)["spec"],
                    headers={"Idempotency-Key": "retired"},
                )
            ).status_code == 409

    asyncio.run(exercise())


def test_orchestrator_owns_capacity(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with stack(tmp_path, max_sandboxes=1) as (client, _orchestrator, _daemon, _clock):
            identifier = await artifact(client)
            await create(client, identifier)
            assert (
                await client.post(
                    "/v1/sandboxes",
                    json=request(identifier)["spec"],
                    headers={"Idempotency-Key": "second"},
                )
            ).status_code == 429

    asyncio.run(exercise())
