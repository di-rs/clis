"""Conservative physical reconciliation, using actual IDs after ownership checks."""

import asyncio
from collections.abc import Callable

from boxie.domain.models import SandboxState, TerminationReason
from boxie.sandbox.docker import ContainerInfo, ExecutionDocker
from boxie.sandbox.execution import ExecutionRecord


async def owned_container(docker: ExecutionDocker, record: ExecutionRecord) -> ContainerInfo | None:
    info = await docker.inspect(record.container_id or record.name)
    if info is not None and any(info.labels.get(k) != v for k, v in record.labels.items()):
        raise RuntimeError("container ownership mismatch")
    return info


async def reconcile_removal(
    docker: ExecutionDocker,
    record: ExecutionRecord,
    save: Callable[[], None],
    timeout: float,
    create_pending: bool,
) -> None:
    """A missing name never resolves a lost create; only its result can do that.

    Finding the owned deterministic resource after transport/process loss resolves
    that single create. While an in-process create remains pending, even deletion
    of an existing resource cannot prove that the pending call has settled.
    """
    try:
        async with asyncio.timeout(timeout):
            info = await owned_container(docker, record)
            if info is not None:
                record.container_id = info.id
                save()
                await docker.delete(info.id)
                if await docker.inspect(info.id) is not None:
                    return
                record.container_id = None
                if not create_pending:
                    record.creation_uncertain = False
            if create_pending or record.creation_uncertain:
                return
            record.container_id = None
            record.cleanup_pending = False
            record.removal_confirmed = True
            record.start_uncertain = False
            record.state = (
                SandboxState.FAILED
                if record.reason == TerminationReason.PROVISIONING_FAILED
                else SandboxState.TERMINATED
            )
    except OSError, RuntimeError, TimeoutError:
        pass
    except Exception:
        # Includes Docker transport/status failures. Never turn them into absence.
        pass
    finally:
        record.event_version += 1
        save()
