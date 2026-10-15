"""Trusted profile reloads freeze admitted versions and reject unsafe configuration."""

import json
from pathlib import Path

import pytest
from boxie.domain.errors import BoxieError
from boxie.sandbox.profiles import ProfileRegistry
from boxie_test_support.agent import IMAGE, configuration
from integration.conftest import postgres_dsn as postgres_dsn
from integration.conftest import postgres_server as postgres_server


def test_reload_is_atomic_and_retains_approved_snapshot(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    profiles = ProfileRegistry(config.profiles_file, config.data_dir / "profiles")
    original = profiles.resolve_profile("shell", ("/bin/sh",), {"TERM": "xterm-256color"})
    assert original.image_digest == IMAGE
    data = json.loads(config.profiles_file.read_text())
    data["shell"]["image_digest"] = "example.org/shell@sha256:" + "2" * 64
    config.profiles_file.write_text(json.dumps(data))
    profiles.reload()
    assert profiles.resolve_profile("shell", ("/bin/sh",), {}).image_digest != IMAGE
    assert profiles.validate_snapshot(original) == original
    restarted = ProfileRegistry(config.profiles_file, config.data_dir / "profiles")
    assert restarted.validate_snapshot(original) == original
    data["shell"]["limits"] = {"memory_bytes": 536870913}
    config.profiles_file.write_text(json.dumps(data))
    with pytest.raises(BoxieError):
        profiles.reload()
    assert profiles.resolve_profile("shell", ("/bin/sh",), {}).image_digest.endswith("2" * 64)


@pytest.mark.parametrize(
    "patch",
    [
        {"image_digest": "shell:latest"},
        {"artifact_target": "/etc"},
        {"commands": []},
        {"commands": [[""]]},
        {"env": {"BAD=KEY": ["value"]}},
        {"runtime": "runc"},
        {"limits": {"output_bytes": 0}},
    ],
)
def test_invalid_profiles_fail_closed(tmp_path: Path, patch: dict[str, object]) -> None:
    config = configuration(tmp_path)
    data = json.loads(config.profiles_file.read_text())
    data["shell"].update(patch)
    config.profiles_file.write_text(json.dumps(data))
    with pytest.raises(BoxieError):
        ProfileRegistry(config.profiles_file, config.data_dir / "profiles")


def test_local_content_addressed_image_is_approved(tmp_path: Path) -> None:
    config = configuration(tmp_path)
    data = json.loads(config.profiles_file.read_text())
    data["shell"]["image_digest"] = "sha256:" + "3" * 64
    config.profiles_file.write_text(json.dumps(data))
    profiles = ProfileRegistry(config.profiles_file, config.data_dir / "profiles")
    assert profiles.resolve_profile("shell", ("/bin/sh",), {}).image_digest == "sha256:" + "3" * 64


@pytest.mark.integration
def test_service_sighup_reload_is_atomic(tmp_path: Path, postgres_dsn: str) -> None:
    import asyncio
    import signal
    import socket
    import sys

    import httpx
    from boxie_test_support.agent import AUTH

    async def exercise() -> None:
        from boxie.persistence.migrate import migrate
        from test_postgres import MIGRATIONS

        await asyncio.to_thread(migrate, postgres_dsn, MIGRATIONS)
        config = configuration(tmp_path)
        dsn_file = tmp_path / "postgres.secret"
        dsn_file.write_text(postgres_dsn)
        dsn_file.chmod(0o600)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        settings = tmp_path / "agent.json"
        settings.write_text(
            json.dumps(
                {
                    "data_dir": str(config.data_dir),
                    "profiles_file": str(config.profiles_file),
                    "credential_file": str(config.credential_file),
                    "identity": {
                        "installation_id": str(config.identity.installation_id),
                        "host_id": "host-1",
                    },
                    "bind_port": port,
                    "postgres_dsn_file": str(dsn_file),
                    "api_url": "http://127.0.0.1:1",
                }
            )
        )
        with (tmp_path / "agent.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "boxie.bootstrap.agent",
                "--config",
                str(settings),
                stdout=log,
                stderr=log,
            )
            try:
                async with httpx.AsyncClient(
                    base_url=f"http://127.0.0.1:{port}", headers=AUTH
                ) as client:
                    payload = {"profile_id": "shell", "command": ["/bin/sh"], "env": {}}
                    for _ in range(200):
                        try:
                            first = await client.post("/v1/profiles/resolve", json=payload)
                            break
                        except httpx.ConnectError:
                            await asyncio.sleep(0.01)
                    else:
                        raise AssertionError((tmp_path / "agent.log").read_text())
                    assert first.json()["image_digest"] == IMAGE
                    data = json.loads(config.profiles_file.read_text())
                    replacement = "sha256:" + "4" * 64
                    data["shell"]["image_digest"] = replacement
                    config.profiles_file.write_text(json.dumps(data))
                    process.send_signal(signal.SIGHUP)
                    for _ in range(200):
                        result = await client.post("/v1/profiles/resolve", json=payload)
                        if result.json()["image_digest"] == replacement:
                            break
                        await asyncio.sleep(0.01)
                    assert result.json()["image_digest"] == replacement
                    config.profiles_file.write_text('{"shell":{"image_digest":"untrusted:tag"}}')
                    process.send_signal(signal.SIGHUP)
                    await asyncio.sleep(0.05)
                    assert (await client.post("/v1/profiles/resolve", json=payload)).json()[
                        "image_digest"
                    ] == replacement
            finally:
                process.terminate()
                await asyncio.wait_for(process.wait(), 5)
        assert "agent-test-secret" not in (tmp_path / "agent.log").read_text()

    asyncio.run(exercise())
