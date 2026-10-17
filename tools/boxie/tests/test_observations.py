"""Live lifecycle edges publish directly; input does not create delivery work."""

import asyncio
from pathlib import Path

from boxie.domain.lifecycle import OrchestratorObservation
from boxie.domain.models import SandboxState
from lifecycle_support import settled, setup


def test_attachment_publishes_directly_and_failed_delivery_does_not_undo_it(tmp_path: Path) -> None:
    async def exercise() -> None:
        orchestrator, request, _, _, _ = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        observed: list[OrchestratorObservation] = []
        unavailable = False

        async def project(fact: OrchestratorObservation) -> None:
            if unavailable:
                raise OSError("SQL unavailable")
            observed.append(fact)

        orchestrator.observer = project
        try:
            epoch = await orchestrator.attach(request.ref, attachment_order=1)
            assert observed[-1].attachment_epoch == epoch
            assert observed[-1].disconnected_at is None
            await orchestrator.write(request.ref, epoch, b"active")
            assert len(observed) == 1
            unavailable = True
            await orchestrator.detach(request.ref, epoch)
            assert (await orchestrator.inspect(request.ref)).disconnected_at is not None
            unavailable = False
            await orchestrator.publish(request.ref)
            assert observed[-1].disconnected_at is not None
            assert observed[-1].last_input_at is not None
        finally:
            await orchestrator.close()

    asyncio.run(exercise())
