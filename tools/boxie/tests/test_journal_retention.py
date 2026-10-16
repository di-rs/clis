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
from test_agent_lifecycle import settled, setup


def test_input_is_coalesced_but_critical_boundary_forces_latest(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, _, clock, _ = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        epoch = await agent.attach(request.ref, attachment_order=1)
        before = agent.journal.path.read_bytes()
        for _ in range(100):
            clock.advance_monotonic(0.001)
            await agent.write(request.ref, epoch, b"x")
        assert agent.journal.path.read_bytes() == before
        assert (await agent.inspect(request.ref)).event_version > json.loads(before)["records"][0][
            "event_version"
        ]
        await agent.detach(request.ref, epoch)
        assert (
            json.loads(agent.journal.path.read_bytes())["records"][0]["last_input_at"] is not None
        )
        await agent.close()

    asyncio.run(exercise())


def test_final_record_requires_receipt_and_settled_sessions(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, _, _, guard = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        await agent.remove(request.ref, TerminationReason.REQUESTED)
        await asyncio.gather(*agent.workers.values())
        await agent.compact()
        assert request.ref.id in agent.records
        guard.receipt = True
        agent.sessions[request.ref.id] = 1
        await agent.compact()
        assert request.ref.id in agent.records
        agent.sessions.clear()
        await agent.compact()
        assert not agent.records and not agent.locks and not agent.workers
        assert json.loads(agent.journal.path.read_bytes())["records"] == []
        guard.missing = True
        with pytest.raises(BoxieError):
            await agent.ensure(request)
        assert not agent.records and not agent.locks
        with pytest.raises(BoxieError):
            await agent.inspect(SandboxRef(uuid4(), 1))
        assert not agent.locks
        await agent.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("after_replace", [False, True])
def test_compaction_failure_preserves_barrier_and_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, after_replace: bool
) -> None:
    async def exercise() -> None:
        agent, request, _, _, guard = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        await agent.remove(request.ref, TerminationReason.REQUESTED)
        await asyncio.gather(*agent.workers.values())
        guard.receipt = True
        save = agent.journal.save

        def fail() -> None:
            if after_replace:
                save()
            raise OSError("owned journal unavailable")

        monkeypatch.setattr(agent.journal, "save", fail)
        await agent.compact()
        assert request.ref.id in agent.records and not agent.execution_ready
        from boxie.sandbox.execution import ExecutionJournal

        recovered = ExecutionJournal(agent.journal.path, agent.config.identity)
        assert (request.ref.id in recovered.records) is not after_replace
        # An ambiguous successful replacement is safe across process loss: fresh
        # ensure still consults the immutable SQL guard and cannot resurrect it.
        if after_replace:
            from boxie.sandbox.lifecycle import DurableAgent

            replacement = DurableAgent(
                agent.config,
                agent.docker,
                agent.artifact_path,
                guard,
                agent.clock,
                agent.monotonic,
            )
            guard.missing = True
            with pytest.raises(BoxieError):
                await replacement.ensure(request)
            assert not replacement.records
            await replacement.close()
        monkeypatch.setattr(agent.journal, "save", save)
        agent._save()
        await agent.compact()
        assert not agent.records and agent.execution_ready
        await agent.close()

    asyncio.run(exercise())


def test_admission_ceiling_serializes_live_and_cleanup_headroom(tmp_path: Path) -> None:
    from dataclasses import replace

    from boxie.sandbox.execution import ExecutionRecord

    async def exercise() -> None:
        agent, request, _, _, guard = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        await agent.remove(request.ref, TerminationReason.REQUESTED)
        original = agent.records[request.ref.id]
        # Retained final history models unavailable receipt collection, independently
        # of SQL's released slots. No age or count eviction may remove these records.
        for _ in range(125):
            record = ExecutionRecord.model_validate(original.model_dump())
            record.id = uuid4()
            agent.records[record.id] = record
        agent._save()
        requests = [replace(request, ref=SandboxRef(uuid4(), 1)) for _ in range(10)]
        results = await asyncio.gather(*(agent.ensure(r) for r in requests), return_exceptions=True)
        assert len(agent.records) == 128
        assert sum(isinstance(r, BoxieError) and r.status == 503 for r in results) == 8
        guard.stopped = True
        cleanup = [replace(request, ref=SandboxRef(uuid4(), 1)) for _ in range(12)]
        results = await asyncio.gather(*(agent.ensure(r) for r in cleanup), return_exceptions=True)
        assert len(agent.records) == 136 and not agent.execution_ready
        assert sum(isinstance(r, BoxieError) and r.status == 503 for r in results) == 4
        await asyncio.gather(*agent.workers.values())
        more = replace(request, ref=SandboxRef(uuid4(), 1))
        with pytest.raises(BoxieError) as error:
            await agent.ensure(more)
        assert error.value.status == 503 and len(agent.records) == 136
        await agent.close()

    asyncio.run(exercise())


def test_report_response_after_forgetting_cannot_recreate_state(tmp_path: Path) -> None:
    import httpx
    from boxie.sandbox.reporting import ObservationReporter

    async def exercise() -> None:
        agent, request, _, _, _ = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        entered, release = asyncio.Event(), asyncio.Event()

        async def reply(message: httpx.Request) -> httpx.Response:
            entered.set()
            await release.wait()
            return httpx.Response(204)

        reporter = ObservationReporter("http://owned", b"unused")
        await reporter.client.aclose()
        reporter.client = httpx.AsyncClient(
            base_url="http://owned", transport=httpx.MockTransport(reply)
        )
        reporter.offer(await agent.inspect(request.ref))
        task = asyncio.create_task(reporter.flush())
        await entered.wait()
        reporter.forget(request.ref.id)
        release.set()
        await task
        assert not reporter.latest and not reporter.sent
        await reporter.close()
        await agent.close()

    asyncio.run(exercise())


def test_continuous_input_flushes_on_schedule_without_postponement(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, _, clock, _ = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        await agent.start()
        epoch = await agent.attach(request.ref, attachment_order=1)
        before = agent.journal.path.read_bytes()
        for _ in range(11):
            await agent.write(request.ref, epoch, b"x")
            clock.advance(0.1)
            await asyncio.sleep(0.012)
        after = json.loads(agent.journal.path.read_bytes())["records"][0]
        assert after["event_version"] > json.loads(before)["records"][0]["event_version"]
        assert after["last_input_at"] is not None
        await agent.close()

    asyncio.run(exercise())


def test_final_reader_callback_settles_before_forgetting(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, _, guard = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        terminal = agent.terminals[request.ref.id]
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
            await agent.compact()
            assert request.ref.id in agent.records
        finally:
            release.set()
            assert terminal.reader is not None
            await terminal.reader
        await agent.compact()
        assert not agent.records
        await agent.close()

    asyncio.run(exercise())


def test_active_final_protocol_session_blocks_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from uuid import UUID

    from boxie.sandbox import terminal_session
    from boxie_test_support.api import CONSUMER_AUTH, artifact, create, ready, terminal_url
    from boxie_test_support.terminal_network import attach, network
    from websockets.asyncio.client import connect

    async def exercise() -> None:
        async with network(tmp_path) as net:
            identifier = await create(net.api, await artifact(net.api))
            await ready(net.api, identifier)
            ref = net.agent.records[UUID(identifier)].ref
            entered, release = asyncio.Event(), asyncio.Event()
            close = terminal_session.close_socket

            async def held_close(socket: Any, code: int = 1000) -> None:
                entered.set()
                await release.wait()
                await close(socket, code)

            monkeypatch.setattr(terminal_session, "close_socket", held_close)
            net.agent.guard.receipt = True
            async with connect(
                terminal_url(net.api, identifier), additional_headers=CONSUMER_AUTH
            ) as socket:
                await attach(socket, order=1)
                await net.agent.remove(ref, TerminationReason.REQUESTED)
                await asyncio.wait_for(entered.wait(), 2)
                await net.agent.compact()
                assert ref.id in net.agent.records and net.agent.sessions[ref.id] == 1
                release.set()
            for _ in range(100):
                if not net.agent.sessions:
                    break
                await asyncio.sleep(0.01)
            assert not net.agent.sessions
            await net.agent.compact()
            assert not net.agent.records

    asyncio.run(exercise())


def test_unknown_create_deleted_during_failed_save_remains_uncertain_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        from boxie.sandbox.lifecycle import DurableAgent

        agent, request, daemon, clock, guard = await setup(tmp_path)
        daemon.create_gate = asyncio.Event()
        daemon.lost_ack = True
        await daemon.create_entered.wait()
        agent.workers[request.ref.id].cancel()
        await asyncio.gather(agent.workers[request.ref.id], return_exceptions=True)
        daemon.create_gate.set()
        await agent.creates[request.ref.id]
        assert daemon.resources and not daemon.started
        disk = agent.journal.path.read_bytes()
        assert json.loads(disk)["records"][0]["creation_uncertain"]

        def unavailable() -> None:
            raise OSError("persistent journal outage")

        for _ in range(2):
            replacement = DurableAgent(
                agent.config,
                daemon,
                agent.artifact_path,
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
        recovered = DurableAgent(
            agent.config,
            daemon,
            agent.artifact_path,
            guard,
            clock,
            clock.monotonic,
        )
        await recovered.start()
        pending = await recovered.inspect(request.ref)
        assert pending.cleanup_pending and not pending.removal_confirmed
        assert recovered.records[request.ref.id].creation_uncertain
        assert not recovered.terminals and not daemon.started
        await recovered.compact()
        assert request.ref.id in recovered.records
        await recovered.close()

    asyncio.run(exercise())


def test_receipt_error_and_changed_version_never_authorize_forgetting(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, _, _, guard = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        await agent.remove(request.ref, TerminationReason.REQUESTED)
        await asyncio.gather(*agent.workers.values())

        async def unavailable(request: Any) -> bool:
            raise OSError("receipt unavailable")

        guard.terminal_receipt = unavailable  # type: ignore[method-assign]
        await agent.compact()
        assert request.ref.id in agent.records
        entered, release = asyncio.Event(), asyncio.Event()

        async def held(request: Any) -> bool:
            entered.set()
            await release.wait()
            return True

        guard.terminal_receipt = held  # type: ignore[method-assign]
        task = asyncio.create_task(agent.compact())
        await entered.wait()
        agent.records[request.ref.id].event_version += 1
        agent._save()
        release.set()
        await task
        assert request.ref.id in agent.records
        await agent.compact()
        assert not agent.records
        await agent.close()

    asyncio.run(exercise())


def test_failed_cleanup_initialization_keeps_pending_without_creating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    async def exercise() -> None:
        agent, first, daemon, _, guard = await setup(tmp_path)
        await settled(agent, first.ref, SandboxState.READY)
        await agent.remove(first.ref, TerminationReason.REQUESTED)
        guard.stopped = True
        request = replace(first, ref=SandboxRef(uuid4(), 1))
        save = agent.journal.save

        def unavailable() -> None:
            raise OSError("initial cleanup save unavailable")

        monkeypatch.setattr(agent.journal, "save", unavailable)
        with pytest.raises(BoxieError) as error:
            await agent.ensure(request)
        assert error.value.status == 503
        pending = await agent.inspect(request.ref)
        assert pending.cleanup_pending and not pending.removal_confirmed
        await agent.reconcile()
        assert not (await agent.inspect(request.ref)).removal_confirmed
        assert daemon.creates == 1 and not agent.execution_ready
        monkeypatch.setattr(agent.journal, "save", save)
        agent._save()
        await agent.reconcile()
        assert (await agent.inspect(request.ref)).removal_confirmed
        assert daemon.creates == 1
        await agent.close()

    asyncio.run(exercise())
