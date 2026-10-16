"""Typed execution setup shared by lifecycle, retention and terminal tests."""

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from boxie.domain.lifecycle import EnsureRequest, OrchestratorObservation, SandboxRef
from boxie.domain.models import Principal, SandboxSpec, SandboxState
from boxie.sandbox.execution.orchestrator import DurableOrchestrator
from boxie_test_support.durable import Daemon, Guard
from boxie_test_support.orchestrator import Clock, configuration


async def setup(
    root: Path, **changes: Any
) -> tuple[DurableOrchestrator, EnsureRequest, Daemon, Clock, Guard]:
    config = configuration(root, **changes)
    clock, daemon, guard = Clock(), Daemon(), Guard()

    async def artifact(principal: Principal, identifier: UUID) -> Path:
        return root

    orchestrator = DurableOrchestrator(config, daemon, artifact, guard, clock, clock.monotonic)
    spec = SandboxSpec(uuid4(), "shell", ("/bin/sh",), {})
    snapshot = orchestrator.profiles.resolve_profile(spec.profile_id, spec.command, spec.env)
    request = EnsureRequest(
        SandboxRef(uuid4(), 1),
        Principal("one"),
        config.identity,
        spec,
        snapshot,
        clock(),
        clock() + timedelta(seconds=3600),
    )
    await orchestrator.ensure(request)
    return orchestrator, request, daemon, clock, guard


async def settled(
    orchestrator: DurableOrchestrator, ref: SandboxRef, state: SandboxState
) -> OrchestratorObservation:
    for _ in range(200):
        observation = await orchestrator.inspect(ref)
        if observation.state == state:
            return observation
        await asyncio.sleep(0.001)
    raise AssertionError(observation)
