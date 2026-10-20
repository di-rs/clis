"""Consumer creation fixes the environment before execution admission."""

import asyncio
from pathlib import Path
from uuid import UUID

from boxie_test_support.api import artifact
from boxie_test_support.terminal_network import network


def test_artifact_only_creation_resolves_boxie_default(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with network(tmp_path) as net:
            artifact_id = await artifact(net.api)
            response = await net.api.post(
                "/v1/sandboxes",
                json={"artifact_id": artifact_id},
                headers={"Idempotency-Key": "default"},
            )
            assert response.status_code == 202, response.text
            record = net.supervisor.records[UUID(response.json()["id"])]
            assert str(record.spec.artifact_id) == artifact_id
            assert record.spec.profile_id == record.snapshot.profile_id == "default"
            assert record.spec.command == record.snapshot.command == ("/opt/boxie/shell.sh",)
            assert record.spec.env == record.snapshot.env == {"TERM": "xterm-256color"}
            assert record.spec.policy.max_seconds == 3600
            assert record.spec.policy.idle_seconds == 600
            assert record.spec.policy.disconnect_seconds == 120

    asyncio.run(exercise())


def test_creation_rejects_environment_and_lifetime_overrides(tmp_path: Path) -> None:
    async def exercise() -> None:
        async with network(tmp_path) as net:
            artifact_id = await artifact(net.api)
            overrides: tuple[dict[str, object], ...] = (
                {"profile_id": "default"},
                {"command": ["/opt/boxie/shell.sh"]},
                {"env": {"TERM": "xterm-256color"}},
                {"policy": {}},
                {"image": "arbitrary"},
                {"host_path": "/etc"},
                {"consumer_id": "two"},
                {"profile_id": "shell", "command": ["/bin/sh"], "env": {}},
            )
            for patch in overrides:
                response = await net.api.post(
                    "/v1/sandboxes",
                    json={"artifact_id": artifact_id, **patch},
                    headers={"Idempotency-Key": "override"},
                )
                assert response.status_code == 422, response.text
                assert response.json()["detail"] == "invalid request"
            assert not net.supervisor.records

    asyncio.run(exercise())
