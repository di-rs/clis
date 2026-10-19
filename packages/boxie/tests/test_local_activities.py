"""Local activity execution preserves a single physical runtime across retries."""

import asyncio
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from boxie.domain.models import SandboxState, TerminationReason
from lifecycle_support import settled, setup


def test_initialization_does_not_spawn_provisioning(tmp_path: Path) -> None:
    async def exercise() -> None:
        orchestrator, first, daemon, _, _ = await setup(tmp_path)
        await settled(orchestrator, first.ref, SandboxState.READY)
        second = replace(first, ref=replace(first.ref, id=uuid4()))
        try:
            await orchestrator.initialize(second)
            await asyncio.sleep(0.01)
            assert daemon.creates == 1
            await asyncio.gather(*(orchestrator.ensure_ready(second) for _ in range(4)))
            assert daemon.creates == 2
            assert len(daemon.started) == 2
            terminal = orchestrator.runtimes[second.ref.id].pty
            await orchestrator.ensure_ready(second)
            assert orchestrator.runtimes[second.ref.id].pty is terminal
            assert len(daemon.started) == 2
        finally:
            await orchestrator.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("timeout_first", [False, True])
def test_cancelled_provision_keeps_pending_create_fenced(
    tmp_path: Path, timeout_first: bool
) -> None:
    async def exercise() -> None:
        orchestrator, first, daemon, _, _ = await setup(
            tmp_path, docker_timeout=0.03 if timeout_first else 20
        )
        await settled(orchestrator, first.ref, SandboxState.READY)
        second = replace(first, ref=replace(first.ref, id=uuid4()))
        daemon.create_gate = asyncio.Event()
        daemon.create_entered.clear()
        try:
            task = asyncio.create_task(orchestrator.ensure_ready(second))
            await asyncio.wait_for(daemon.create_entered.wait(), 1)
            if timeout_first:
                await asyncio.sleep(0.05)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            pending_create = orchestrator.runtimes[second.ref.id].create
            assert pending_create is not None and not pending_create.cancelled()
            pending = await orchestrator.remove(second.ref, TerminationReason.REQUESTED)
            assert pending.cleanup_pending and not pending.removal_confirmed
            daemon.create_gate.set()
            await pending_create
            await orchestrator.remove(second.ref, TerminationReason.REQUESTED)
            assert (await orchestrator.inspect(second.ref)).removal_confirmed
            assert len(daemon.started) == 1
        finally:
            daemon.create_gate.set()
            await orchestrator.close()

    asyncio.run(exercise())


def test_retry_after_cancelled_start_reuses_terminal_and_observed_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        from boxie.sandbox.docker import ContainerInfo

        orchestrator, first, daemon, _, _ = await setup(tmp_path)
        await settled(orchestrator, first.ref, SandboxState.READY)
        second = replace(first, ref=replace(first.ref, id=uuid4()))
        started, release = asyncio.Event(), asyncio.Event()
        original_start = daemon.start

        async def delayed_ack(identifier: str) -> None:
            await original_start(identifier)
            for name, info in daemon.resources.items():
                if info.id == identifier:
                    daemon.resources[name] = ContainerInfo(
                        id=info.id, labels=info.labels, running=True
                    )
            started.set()
            await release.wait()

        monkeypatch.setattr(daemon, "start", delayed_ack)
        task = asyncio.create_task(orchestrator.ensure_ready(second))
        try:
            await asyncio.wait_for(started.wait(), 1)
            terminal = orchestrator.runtimes[second.ref.id].pty
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            release.set()
            observation = await orchestrator.ensure_ready(second)
            assert observation.state == SandboxState.READY
            assert orchestrator.runtimes[second.ref.id].pty is terminal
            assert len(daemon.started) == 2, "retry repeated a physically completed Docker start"
        finally:
            release.set()
            await orchestrator.close()

    asyncio.run(exercise())
