"""Local finality, durable receipt and bounded input persistence contracts."""

import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from boxie.domain.errors import BoxieError
from boxie.domain.lifecycle import SandboxRef
from boxie.domain.models import SandboxState, TerminationReason
from boxie_test_support.durable import Guard, launch_execution
from lifecycle_support import settled, setup


def test_input_is_coalesced_but_critical_boundary_forces_latest(tmp_path: Path) -> None:
    async def exercise() -> None:
        orchestrator, request, _, clock, _ = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        epoch = await orchestrator.attach(request.ref, attachment_order=1)
        before = orchestrator.journal.path.read_bytes()
        for _ in range(100):
            clock.advance_monotonic(0.001)
            await orchestrator.write(request.ref, epoch, b"x")
        assert orchestrator.journal.path.read_bytes() == before
        assert (await orchestrator.inspect(request.ref)).event_version > json.loads(before)[
            "records"
        ][0]["event_version"]
        await orchestrator.detach(request.ref, epoch)
        assert (
            json.loads(orchestrator.journal.path.read_bytes())["records"][0]["last_input_at"]
            is not None
        )
        await orchestrator.close()

    asyncio.run(exercise())


def test_final_record_requires_receipt_and_settled_sessions(tmp_path: Path) -> None:
    async def exercise() -> None:
        orchestrator, request, _, _, guard = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        await orchestrator.remove(request.ref, TerminationReason.REQUESTED)
        await asyncio.gather(
            *(
                runtime.activity
                for runtime in orchestrator.runtimes.values()
                if runtime.activity is not None
            )
        )
        await orchestrator.compact()
        assert request.ref.id in orchestrator.records
        guard.receipt = True
        orchestrator.runtimes[request.ref.id].sessions = 1
        await orchestrator.compact()
        assert request.ref.id in orchestrator.records
        orchestrator.runtimes[request.ref.id].sessions = 0
        await orchestrator.compact()
        assert not orchestrator.records and not orchestrator.runtimes
        assert json.loads(orchestrator.journal.path.read_bytes())["records"] == []
        guard.missing = True
        with pytest.raises(BoxieError):
            await launch_execution(orchestrator, request)
        assert not orchestrator.records and not orchestrator.runtimes
        with pytest.raises(BoxieError):
            await orchestrator.inspect(SandboxRef(uuid4(), 1))
        assert not orchestrator.runtimes
        await orchestrator.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("after_replace", [False, True])
def test_compaction_failure_preserves_barrier_and_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_replace: bool
) -> None:
    async def exercise() -> None:
        orchestrator, request, _, _, guard = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        await orchestrator.remove(request.ref, TerminationReason.REQUESTED)
        await asyncio.gather(
            *(
                runtime.activity
                for runtime in orchestrator.runtimes.values()
                if runtime.activity is not None
            )
        )
        guard.receipt = True
        save = orchestrator.journal.save

        def fail() -> None:
            if after_replace:
                save()
            raise OSError("owned journal unavailable")

        monkeypatch.setattr(orchestrator.journal, "save", fail)
        await orchestrator.compact()
        assert request.ref.id in orchestrator.records and not orchestrator.execution_ready
        from boxie.sandbox.execution.journal import ExecutionJournal

        recovered = ExecutionJournal(orchestrator.journal.path, orchestrator.config.identity)
        assert (request.ref.id in recovered.records) is not after_replace
        # An ambiguous successful replacement is safe across process loss: fresh
        # ensure still consults the immutable SQL guard and cannot resurrect it.
        if after_replace:
            from boxie.sandbox.execution.orchestrator import DurableOrchestrator

            replacement = DurableOrchestrator(
                orchestrator.config,
                orchestrator.docker,
                orchestrator.artifact_path,
                guard,
                orchestrator.clock,
                orchestrator.monotonic,
            )
            guard.missing = True
            with pytest.raises(BoxieError):
                await launch_execution(replacement, request)
            assert not replacement.records
            await replacement.close()
        monkeypatch.setattr(orchestrator.journal, "save", save)
        orchestrator._save()
        await orchestrator.compact()
        assert not orchestrator.records and orchestrator.execution_ready
        await orchestrator.close()

    asyncio.run(exercise())


def test_admission_ceiling_serializes_live_and_cleanup_headroom(tmp_path: Path) -> None:
    from dataclasses import replace

    from boxie.sandbox.execution.journal import ExecutionRecord
    from boxie.sandbox.execution.runtime import ExecutionRuntime

    async def exercise() -> None:
        orchestrator, request, _, _, guard = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        await orchestrator.remove(request.ref, TerminationReason.REQUESTED)
        original = orchestrator.records[request.ref.id]
        # Retained final history models unavailable receipt collection, independently
        # of SQL's released slots. No age or count eviction may remove these records.
        for _ in range(125):
            record = ExecutionRecord.model_validate(original.model_dump())
            record.id = uuid4()
            orchestrator.records[record.id] = record
            orchestrator.runtimes[record.id] = ExecutionRuntime(orchestrator, record)
        orchestrator._save()
        requests = [replace(request, ref=SandboxRef(uuid4(), 1)) for _ in range(10)]
        results = await asyncio.gather(
            *(launch_execution(orchestrator, r) for r in requests), return_exceptions=True
        )
        assert len(orchestrator.records) == 128
        assert sum(isinstance(r, BoxieError) and r.status == 503 for r in results) == 8
        guard.stopped = True
        cleanup = [replace(request, ref=SandboxRef(uuid4(), 1)) for _ in range(12)]
        results = await asyncio.gather(
            *(launch_execution(orchestrator, r) for r in cleanup), return_exceptions=True
        )
        assert len(orchestrator.records) == 136 and not orchestrator.execution_ready
        assert sum(isinstance(r, BoxieError) and r.status == 503 for r in results) == 4
        await asyncio.gather(
            *(
                runtime.activity
                for runtime in orchestrator.runtimes.values()
                if runtime.activity is not None
            )
        )
        more = replace(request, ref=SandboxRef(uuid4(), 1))
        with pytest.raises(BoxieError) as error:
            await launch_execution(orchestrator, more)
        assert error.value.status == 503 and len(orchestrator.records) == 136
        await orchestrator.close()

    asyncio.run(exercise())


def test_continuous_input_flushes_on_schedule_without_postponement(tmp_path: Path) -> None:
    async def exercise() -> None:
        orchestrator, request, _, clock, _ = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        await orchestrator.start()
        epoch = await orchestrator.attach(request.ref, attachment_order=1)
        before = orchestrator.journal.path.read_bytes()
        for _ in range(11):
            await orchestrator.write(request.ref, epoch, b"x")
            clock.advance(0.1)
            await asyncio.sleep(0.012)
        after = json.loads(orchestrator.journal.path.read_bytes())["records"][0]
        assert after["event_version"] > json.loads(before)["records"][0]["event_version"]
        assert after["last_input_at"] is not None
        await orchestrator.close()

    asyncio.run(exercise())


def test_final_reader_callback_settles_before_forgetting(tmp_path: Path) -> None:
    async def exercise() -> None:
        orchestrator, request, daemon, _, guard = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        terminal = orchestrator.runtimes[request.ref.id].pty
        original = terminal.terminate
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(reason: TerminationReason) -> Any:
            result = await original(reason)
            entered.set()
            await release.wait()
            return result

        terminal.terminate = delayed
        guard.receipt = True
        next(iter(daemon.streams.values())).queue.put_nowait(b"")
        await entered.wait()
        try:
            await orchestrator.compact()
            assert request.ref.id in orchestrator.records
        finally:
            release.set()
            assert terminal.reader is not None
            await terminal.reader
        await orchestrator.compact()
        assert not orchestrator.records
        await orchestrator.close()

    asyncio.run(exercise())


def test_active_final_protocol_session_blocks_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import UUID

    from boxie.sandbox.terminal import session as terminal_session
    from boxie_test_support.api import CONSUMER_AUTH, artifact, create, ready, terminal_url
    from boxie_test_support.terminal_network import attach, network
    from websockets.asyncio.client import connect

    async def exercise() -> None:
        async with network(tmp_path) as net:
            identifier = await create(net.api, await artifact(net.api))
            await ready(net.api, identifier)
            ref = net.orchestrator.records[UUID(identifier)].ref
            entered, release = asyncio.Event(), asyncio.Event()
            close = terminal_session.close_socket

            async def held_close(socket: Any, code: int = 1000) -> None:
                entered.set()
                await release.wait()
                await close(socket, code)

            monkeypatch.setattr(terminal_session, "close_socket", held_close)
            assert isinstance(net.orchestrator.guard, Guard)
            net.orchestrator.guard.receipt = True
            async with connect(
                terminal_url(net.api, identifier), additional_headers=CONSUMER_AUTH
            ) as socket:
                await attach(socket, order=1)
                await net.orchestrator.remove(ref, TerminationReason.REQUESTED)
                await asyncio.wait_for(entered.wait(), 2)
                await net.orchestrator.compact()
                assert (
                    ref.id in net.orchestrator.records
                    and net.orchestrator.runtimes[ref.id].sessions == 1
                )
                release.set()
            for _ in range(100):
                if not any(r.sessions for r in net.orchestrator.runtimes.values()):
                    break
                await asyncio.sleep(0.01)
            assert not any(r.sessions for r in net.orchestrator.runtimes.values())
            await net.orchestrator.compact()
            assert not net.orchestrator.records

    asyncio.run(exercise())


def test_unknown_create_deleted_during_failed_save_remains_uncertain_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        from boxie.sandbox.execution.orchestrator import DurableOrchestrator

        orchestrator, request, daemon, clock, guard = await setup(tmp_path)
        daemon.create_gate = asyncio.Event()
        daemon.lost_ack = True
        await daemon.create_entered.wait()
        worker = orchestrator.runtimes[request.ref.id].activity
        assert worker is not None
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        daemon.create_gate.set()
        creating = orchestrator.runtimes[request.ref.id].create
        assert creating is not None
        await creating
        assert daemon.resources and not daemon.started
        disk = orchestrator.journal.path.read_bytes()
        assert json.loads(disk)["records"][0]["creation_uncertain"]

        def unavailable() -> None:
            raise OSError("persistent journal outage")

        for _ in range(2):
            replacement = DurableOrchestrator(
                orchestrator.config,
                daemon,
                orchestrator.artifact_path,
                guard,
                clock,
                clock.monotonic,
            )
            monkeypatch.setattr(replacement.journal, "save", unavailable)
            await replacement.start()
            pending = await replacement.inspect(request.ref)
            assert pending.cleanup_pending and not pending.removal_confirmed
            assert not daemon.resources and not replacement.execution_ready
            assert replacement.journal.path.read_bytes() == disk
            assert replacement.sweeper is not None
            replacement.sweeper.cancel()
            await asyncio.gather(replacement.sweeper, return_exceptions=True)
        recovered = DurableOrchestrator(
            orchestrator.config,
            daemon,
            orchestrator.artifact_path,
            guard,
            clock,
            clock.monotonic,
        )
        await recovered.start()
        pending = await recovered.inspect(request.ref)
        assert pending.cleanup_pending and not pending.removal_confirmed
        assert recovered.records[request.ref.id].creation_uncertain
        assert not any(r.terminal for r in recovered.runtimes.values()) and not daemon.started
        await recovered.compact()
        assert request.ref.id in recovered.records
        await recovered.close()

    asyncio.run(exercise())


def test_receipt_error_and_changed_version_never_authorize_forgetting(tmp_path: Path) -> None:
    async def exercise() -> None:
        orchestrator, request, _, _, guard = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        await orchestrator.remove(request.ref, TerminationReason.REQUESTED)
        await asyncio.gather(
            *(
                runtime.activity
                for runtime in orchestrator.runtimes.values()
                if runtime.activity is not None
            )
        )

        async def unavailable(request: Any) -> bool:
            raise OSError("receipt unavailable")

        guard.terminal_receipt = unavailable  # type: ignore[method-assign]
        await orchestrator.compact()
        assert request.ref.id in orchestrator.records
        entered, release = asyncio.Event(), asyncio.Event()

        async def held(request: Any) -> bool:
            entered.set()
            await release.wait()
            return True

        guard.terminal_receipt = held  # type: ignore[method-assign]
        task = asyncio.create_task(orchestrator.compact())
        await entered.wait()
        orchestrator.records[request.ref.id].event_version += 1
        orchestrator._save()
        release.set()
        await task
        assert request.ref.id in orchestrator.records
        await orchestrator.compact()
        assert not orchestrator.records
        await orchestrator.close()

    asyncio.run(exercise())


def test_failed_cleanup_initialization_keeps_pending_without_creating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    async def exercise() -> None:
        orchestrator, first, daemon, _, guard = await setup(tmp_path)
        await settled(orchestrator, first.ref, SandboxState.READY)
        await orchestrator.remove(first.ref, TerminationReason.REQUESTED)
        guard.stopped = True
        request = replace(first, ref=SandboxRef(uuid4(), 1))
        save = orchestrator.journal.save

        def unavailable() -> None:
            raise OSError("initial cleanup save unavailable")

        monkeypatch.setattr(orchestrator.journal, "save", unavailable)
        with pytest.raises(BoxieError) as error:
            await launch_execution(orchestrator, request)
        assert error.value.status == 503
        pending = await orchestrator.inspect(request.ref)
        assert pending.cleanup_pending and not pending.removal_confirmed
        await orchestrator.reconcile()
        assert not (await orchestrator.inspect(request.ref)).removal_confirmed
        assert daemon.creates == 1 and not orchestrator.execution_ready
        monkeypatch.setattr(orchestrator.journal, "save", save)
        orchestrator._save()
        await orchestrator.reconcile()
        assert (await orchestrator.inspect(request.ref)).removal_confirmed
        assert daemon.creates == 1
        await orchestrator.close()

    asyncio.run(exercise())


def test_cancelled_terminal_session_releases_retention_ownership(tmp_path: Path) -> None:
    """Cancellation must release the session pin without discarding cleanup evidence."""
    from lifecycle_support import settled, setup

    async def exercise() -> None:
        orchestrator, request, _, _, guard = await setup(tmp_path)
        await settled(orchestrator, request.ref, SandboxState.READY)
        entered = asyncio.Event()

        async def hold() -> None:
            with orchestrator.terminal_session(request.ref):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(hold())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            await orchestrator.remove(request.ref, TerminationReason.REQUESTED)
            guard.receipt = True
            await orchestrator.compact()
            assert request.ref.id in orchestrator.records
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await orchestrator.compact()
            assert request.ref.id not in orchestrator.records
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await orchestrator.close()

    asyncio.run(exercise())
