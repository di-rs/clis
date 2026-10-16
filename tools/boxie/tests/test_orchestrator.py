import asyncio
from pathlib import Path


def test_durable_provision_preserves_approved_isolation_and_entrypoint(tmp_path: Path) -> None:
    from typing import Any

    from boxie.domain.models import SandboxState
    from boxie_test_support.orchestrator import IMAGE
    from lifecycle_support import settled, setup

    async def exercise() -> None:
        orchestrator, request, daemon, _, _ = await setup(tmp_path)
        original = daemon.create
        captured: list[dict[str, Any]] = []

        async def create(name: str, config: dict[str, Any]) -> None:
            captured.append(config)
            await original(name, config)

        daemon.create = create  # type: ignore[method-assign]
        ready = await settled(orchestrator, request.ref, SandboxState.READY)
        await orchestrator.ensure(request)
        assert daemon.creates == 1 and ready.container_id in daemon.streams
        payload = captured[0]
        assert payload["Image"] == IMAGE
        assert payload["Cmd"] == ["/bin/sh"] and payload["Entrypoint"] == []
        assert payload["User"] == "65534:65534"
        host = payload["HostConfig"]
        assert host["Runtime"] == "runsc" and host["NetworkMode"] == "none"
        assert host["ReadonlyRootfs"] is True and host["CapDrop"] == ["ALL"]
        assert host["SecurityOpt"] == ["no-new-privileges"]
        assert host["Memory"] == host["MemorySwap"] == 536870912
        assert host["NanoCpus"] == 1000000000 and host["PidsLimit"] == 256
        assert host["Ulimits"] == [{"Name": "nofile", "Soft": 1024, "Hard": 1024}]
        assert "size=67108864" in host["Tmpfs"]["/workspace"]
        assert "size=16777216" in host["Tmpfs"]["/tmp"]
        mount = host["Mounts"][0]
        assert mount["Target"] == "/opt/artifact" and mount["ReadOnly"] is True
        assert mount["Source"] == str(tmp_path)
        await orchestrator.close()

    asyncio.run(exercise())
