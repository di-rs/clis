"""Durable execution races with controlled clocks and an external daemon boundary."""

import asyncio
import importlib.util
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from boxie.domain.errors import BoxieError
from boxie.domain.models import Principal, SandboxSpec, SandboxState, TerminationReason
from boxie_test_support.agent import Clock, FakeTerminal, configuration
from boxie_test_support.durable import Daemon as Daemon
from boxie_test_support.durable import Guard as Guard


async def setup(root: Path, **changes: Any) -> tuple[Any, Any, Daemon, Clock, Guard]:
    assert importlib.util.find_spec("boxie.sandbox.lifecycle"), "durable execution missing"
    from boxie.domain.lifecycle import EnsureRequest, SandboxRef
    from boxie.sandbox.lifecycle import DurableAgent

    config = configuration(root, **changes)
    clock, daemon, guard = Clock(), Daemon(), Guard()

    async def artifact(principal: Principal, identifier: Any) -> Path:
        return root

    agent = DurableAgent(config, daemon, artifact, guard, clock, clock.monotonic)
    spec = SandboxSpec(uuid4(), "shell", ("/bin/sh",), {})
    snapshot = agent.profiles.resolve_profile(spec.profile_id, spec.command, spec.env)
    request = EnsureRequest(
        SandboxRef(uuid4(), 1),
        Principal("one"),
        config.identity,
        spec,
        snapshot,
        clock(),
        clock() + timedelta(seconds=3600),
    )
    await agent.ensure(request)
    return agent, request, daemon, clock, guard


async def settled(agent: Any, ref: Any, state: SandboxState) -> Any:
    for _ in range(200):
        observation = await agent.inspect(ref)
        if observation.state == state:
            return observation
        await asyncio.sleep(0.001)
    raise AssertionError(observation)


def test_input_and_attachment_fences_and_absolute_deadline(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, clock, _ = await setup(tmp_path)
        from boxie.domain.lifecycle import ExpiryRequest

        ready = await settled(agent, request.ref, SandboxState.READY)
        epoch = await agent.attach(request.ref, attachment_order=1)
        stale = ExpiryRequest(
            request.ref, TerminationReason.IDLE, ready.event_version, agent.boot_id
        )
        for _ in range(19):
            clock.advance(30)
            await agent.renew_control_lease(request.ref, agent.boot_id)
        clock.advance(29)
        current = await agent.inspect(request.ref)
        stale = replace(stale, observed_event_version=current.event_version)
        await agent.write(request.ref, epoch, b"alive")
        clock.advance(1)
        assert not (await agent.expire_if_current(stale)).applied
        await agent.detach(request.ref, epoch)
        detached = await agent.inspect(request.ref)
        stale_detach = ExpiryRequest(
            request.ref, TerminationReason.DISCONNECTED, detached.event_version, agent.boot_id
        )
        for _ in range(3):
            clock.advance(30)
            await agent.renew_control_lease(request.ref, agent.boot_id)
        clock.advance(29)
        current = await agent.inspect(request.ref)
        stale_detach = replace(stale_detach, observed_event_version=current.event_version)
        newer = await agent.attach(request.ref, attachment_order=2)
        assert newer > epoch
        await agent.detach(request.ref, epoch)
        with pytest.raises(BoxieError):
            await agent.write(request.ref, epoch, b"stale")
        clock.advance(1)
        assert not (await agent.expire_if_current(stale_detach)).applied
        while clock.elapsed < 3570:
            clock.advance(30)
            await agent.renew_control_lease(request.ref, agent.boot_id)
            await agent.write(request.ref, newer, b"active")
        clock.advance(29)
        await agent.write(request.ref, newer, b"last")
        clock.advance(1)
        observation = await agent.inspect(request.ref)
        assert observation.absolute_deadline == request.accepted_at + timedelta(seconds=3600)
        expired = await agent.expire_if_current(replace(stale, reason=TerminationReason.EXPIRED))
        assert expired.applied and expired.observation.removal_confirmed
        assert len(daemon.deleted_ids) == 1
        await agent.close()

    asyncio.run(exercise())


def test_exact_ensure_boot_generation_and_snapshot_fences(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, clock, _ = await setup(tmp_path)
        from boxie.domain.lifecycle import ExpiryRequest, SandboxRef

        await asyncio.gather(*(agent.ensure(request) for _ in range(10)))
        ready = await settled(agent, request.ref, SandboxState.READY)
        assert daemon.creates == 1
        for bad in (
            replace(request, identity=replace(request.identity, host_id="wrong-host")),
            replace(
                request,
                profile_snapshot=replace(
                    request.profile_snapshot, image_digest="sha256:" + "9" * 64
                ),
            ),
            replace(request, ref=SandboxRef(request.ref.id, 2)),
            replace(request, principal=Principal("other")),
            replace(request, absolute_deadline=request.absolute_deadline + timedelta(seconds=1)),
        ):
            with pytest.raises(BoxieError):
                await agent.ensure(bad)
        with pytest.raises(BoxieError):
            await agent.renew_control_lease(request.ref, uuid4())
        assert not (
            await agent.expire_if_current(
                ExpiryRequest(request.ref, TerminationReason.IDLE, ready.event_version, uuid4())
            )
        ).applied
        with pytest.raises(BoxieError):
            await agent.remove(SandboxRef(request.ref.id, 2), TerminationReason.REQUESTED)
        clock.advance_wall(100000)
        assert (await agent.inspect(request.ref)).state == SandboxState.READY
        clock.advance_monotonic(90)
        await agent.reconcile()
        assert (await agent.inspect(request.ref)).reason == TerminationReason.CONTROL_PLANE_LOST
        await agent.close()

    asyncio.run(exercise())


def test_stop_during_late_create_never_confirms_early_absence(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, _, _ = await setup(tmp_path, docker_timeout=0.03)
        daemon.create_gate = asyncio.Event()
        await daemon.create_entered.wait()
        removed = await agent.remove(request.ref, TerminationReason.REQUESTED)
        assert removed.cleanup_pending and not removed.removal_confirmed
        await asyncio.sleep(0.06)
        await agent.reconcile()
        assert (await agent.inspect(request.ref)).cleanup_pending
        daemon.create_gate.set()
        await settled(agent, request.ref, SandboxState.TERMINATED)
        assert not daemon.started and len(daemon.deleted_ids) == 1
        await agent.ensure(request)
        assert daemon.creates == 1
        await agent.close()

    asyncio.run(exercise())


def test_lost_create_ack_and_unreachable_cleanup(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, _, _ = await setup(tmp_path)
        daemon.lost_ack = True
        ready = await settled(agent, request.ref, SandboxState.READY)
        assert daemon.creates == 1 and ready.container_id
        daemon.unreachable = True
        failed = await agent.remove(request.ref, TerminationReason.REQUESTED)
        assert failed.cleanup_pending and not failed.removal_confirmed
        daemon.unreachable = False
        await agent.reconcile()
        assert (await agent.inspect(request.ref)).removal_confirmed
        await agent.close()

    asyncio.run(exercise())


def test_restart_does_not_adopt_pty_or_forget_unknown_create(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, clock, guard = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        from boxie.sandbox.lifecycle import DurableAgent

        replacement = DurableAgent(
            agent.config, daemon, agent.artifact_path, guard, clock, clock.monotonic
        )
        await replacement.start()
        observation = await replacement.inspect(request.ref)
        assert observation.reason == TerminationReason.AGENT_RESTART
        assert observation.removal_confirmed and observation.boot_id != agent.boot_id
        await replacement.ensure(request)
        assert daemon.creates == 1
        await replacement.close()
        await agent.close()

    asyncio.run(exercise())


def test_foreign_name_collision_is_never_attached_started_or_deleted(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, _, _ = await setup(tmp_path)
        from boxie.sandbox.docker import ContainerInfo

        foreign_id = str(uuid4())
        name = agent.records[request.ref.id].name
        daemon.resources[name] = ContainerInfo(foreign_id, {"boxie.installation": "foreign"}, False)
        observation = await settled(agent, request.ref, SandboxState.TERMINATING)
        assert observation.cleanup_pending and not observation.removal_confirmed
        assert not daemon.started and not daemon.streams and daemon.creates == 0
        assert foreign_id not in daemon.deleted_ids
        await agent.close()

    asyncio.run(exercise())


def test_never_attached_grace_and_lease_cannot_be_extended_by_consumer(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, _, clock, _ = await setup(tmp_path)
        ready = await settled(agent, request.ref, SandboxState.READY)
        clock.advance(30)
        renewed = await agent.renew_control_lease(request.ref, agent.boot_id)
        assert renewed.control_lease_until == request.accepted_at + timedelta(seconds=120)
        # Failed websocket acceptance never calls attach: original READY grace remains.
        clock.advance(89)
        assert (await agent.inspect(request.ref)).disconnected_at == ready.ready_at
        epoch = await agent.attach(request.ref, attachment_order=1)
        await agent.write(request.ref, epoch, b"consumer is active")
        clock.advance(1)
        await agent.reconcile()
        observation = await agent.inspect(request.ref)
        assert observation.reason == TerminationReason.CONTROL_PLANE_LOST
        assert observation.removal_confirmed
        with pytest.raises(BoxieError):
            await agent.renew_control_lease(request.ref, agent.boot_id)
        await agent.close()

    asyncio.run(exercise())


def test_stop_during_artifact_lookup_and_after_start_never_publishes_ready(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, _, guard = await setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def artifact(principal: Any, identifier: Any) -> Path:
            entered.set()
            await release.wait()
            return tmp_path

        agent.artifact_path = artifact
        await entered.wait()
        removed = await agent.remove(request.ref, TerminationReason.REQUESTED)
        assert removed.removal_confirmed
        release.set()
        await asyncio.sleep(0.01)
        assert daemon.creates == 0 and not daemon.started
        await agent.close()

        agent, request, daemon, _, guard = await setup(tmp_path / "after")
        original_start = daemon.start

        async def stopped_after_start(identifier: str) -> None:
            await original_start(identifier)
            guard.stopped = True

        daemon.start = stopped_after_start  # type: ignore[method-assign]
        observation = await settled(agent, request.ref, SandboxState.TERMINATED)
        assert observation.ready_at is None and observation.removal_confirmed
        assert len(daemon.started) == 1
        await agent.close()

    asyncio.run(exercise())


def test_restart_retains_unknown_create_then_reconciles_late_resource(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, clock, guard = await setup(tmp_path, docker_timeout=0.02)
        daemon.create_gate = asyncio.Event()
        await daemon.create_entered.wait()
        await agent.remove(request.ref, TerminationReason.REQUESTED)
        # Model process loss: preserve the daemon operation but cancel the waiting worker.
        worker = agent.workers[request.ref.id]
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        from boxie.sandbox.lifecycle import DurableAgent

        replacement = DurableAgent(
            agent.config, daemon, agent.artifact_path, guard, clock, clock.monotonic
        )
        await replacement.start()
        assert (await replacement.inspect(request.ref)).cleanup_pending
        daemon.create_gate.set()
        await asyncio.wait_for(agent.creates[request.ref.id], 1)
        await replacement.reconcile()
        assert (await replacement.inspect(request.ref)).removal_confirmed
        assert not daemon.started
        await replacement.close()

    asyncio.run(exercise())


def test_absolute_expiry_and_stop_cannot_be_deferred_by_blocked_terminal_write(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        agent, request, daemon, clock, _ = await setup(tmp_path, docker_timeout=0.02)
        ready = await settled(agent, request.ref, SandboxState.READY)
        epoch = await agent.attach(request.ref, attachment_order=1)
        entered = asyncio.Event()

        async def blocked_write(data: bytes) -> None:
            entered.set()
            await asyncio.Event().wait()

        daemon.streams[ready.container_id].write = blocked_write  # type: ignore[method-assign]
        write = asyncio.create_task(agent.write(request.ref, epoch, b"blocked"))
        await entered.wait()
        stop = asyncio.create_task(agent.remove(request.ref, TerminationReason.REQUESTED))
        with pytest.raises(TimeoutError):
            await write
        assert (await asyncio.wait_for(stop, 0.2)).removal_confirmed
        await agent.close()

    asyncio.run(exercise())


def test_stop_during_attach_closes_late_stream(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, _, _ = await setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        stream = FakeTerminal()

        async def attach(identifier: str) -> FakeTerminal:
            entered.set()
            await release.wait()
            return stream

        daemon.attach = attach  # type: ignore[method-assign]
        await entered.wait()
        assert (await agent.remove(request.ref, TerminationReason.REQUESTED)).removal_confirmed
        release.set()
        await asyncio.wait_for(agent.workers[request.ref.id], 1)
        assert stream.closed, "late attach must not leak a PTY after confirmed removal"
        assert not daemon.started
        await agent.close()

    asyncio.run(exercise())


def test_slow_cleanup_does_not_delay_another_sandbox_deadline(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, first, daemon, clock, _ = await setup(tmp_path, docker_timeout=0.2)
        from boxie.domain.lifecycle import SandboxRef

        ready = await settled(agent, first.ref, SandboxState.READY)
        second = replace(first, ref=SandboxRef(uuid4(), 1))
        await agent.ensure(second)
        await settled(agent, second.ref, SandboxState.READY)
        await agent.start()
        original = daemon.delete
        entered = asyncio.Event()

        async def slow_delete(identifier: str) -> None:
            if identifier == ready.container_id:
                entered.set()
                await asyncio.Event().wait()
            await original(identifier)

        daemon.delete = slow_delete  # type: ignore[method-assign]
        stop = asyncio.create_task(agent.remove(first.ref, TerminationReason.REQUESTED))
        await entered.wait()
        await asyncio.sleep(0.02)  # Sweeper now also sees the first pending cleanup.
        clock.advance(90)
        await asyncio.sleep(0.04)
        assert (await agent.inspect(second.ref)).removal_confirmed
        await stop
        await agent.close()

    asyncio.run(exercise())


def test_durable_wire_roundtrip_preserves_fences_and_rejects_naive_times(tmp_path: Path) -> None:
    async def exercise() -> None:
        from boxie.sandbox import protocol

        assert hasattr(protocol, "DurableEnsureWire"), "durable request transport schema missing"
        from pydantic import ValidationError

        agent, request, _, _, _ = await setup(tmp_path)
        wire = protocol.DurableEnsureWire.from_domain(request)
        assert (
            protocol.DurableEnsureWire.model_validate_json(wire.model_dump_json()).domain()
            == request
        )
        body = wire.model_dump(mode="json")
        body["accepted_at"] = "2026-10-15T00:00:00"
        with pytest.raises(ValidationError):
            protocol.DurableEnsureWire.model_validate(body)
        ready = await settled(agent, request.ref, SandboxState.READY)
        observation = protocol.ObservationWire.from_domain(ready)
        assert (
            protocol.ObservationWire.model_validate_json(observation.model_dump_json()).domain()
            == ready
        )
        await agent.close()

    asyncio.run(exercise())


def test_ready_grace_does_not_restart_on_retry_or_failed_acceptance(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, clock, _ = await setup(tmp_path)
        ready = await settled(agent, request.ref, SandboxState.READY)
        for _ in range(3):
            clock.advance(30)
            await agent.renew_control_lease(request.ref, agent.boot_id)
            await agent.ensure(request)
        # A failed handshake never invokes attach; inspect/ensure do not reset grace.
        clock.advance(30)
        await agent.reconcile()
        expired = await agent.inspect(request.ref)
        assert expired.reason == TerminationReason.DISCONNECTED
        assert expired.disconnected_at == ready.ready_at and expired.removal_confirmed
        assert len(daemon.started) == 1
        await agent.close()

    asyncio.run(exercise())


def test_delayed_request_uses_original_acceptance_and_approved_snapshot(tmp_path: Path) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        agent, request, daemon, clock, _ = await setup(tmp_path)
        await settled(agent, request.ref, SandboxState.READY)
        await agent.remove(request.ref, TerminationReason.REQUESTED)
        clock.advance(3601)
        delayed = replace(request, ref=SandboxRef(uuid4(), 1))
        await agent.ensure(delayed)
        expired = await settled(agent, delayed.ref, SandboxState.TERMINATED)
        assert expired.reason == TerminationReason.EXPIRED
        assert expired.absolute_deadline == request.absolute_deadline
        assert daemon.creates == 1
        await agent.close()

    asyncio.run(exercise())


def test_output_drain_is_active_before_start_acknowledgement(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, _, _ = await setup(tmp_path)
        started, acknowledged = asyncio.Event(), asyncio.Event()
        original = daemon.start

        async def start(identifier: str) -> None:
            await original(identifier)
            daemon.streams[identifier].queue.put_nowait(b"early output")
            started.set()
            await acknowledged.wait()

        daemon.start = start  # type: ignore[method-assign]
        await started.wait()
        terminal = agent.terminals[request.ref.id]
        try:
            await asyncio.wait_for(terminal.replay.changed.wait(), 0.05)
            assert terminal.replay.after(0)[0].data == b"early output"
        finally:
            acknowledged.set()
            await agent.close()

    asyncio.run(exercise())


def test_control_loss_during_provisioning_prevents_late_start(tmp_path: Path) -> None:
    async def exercise() -> None:
        agent, request, daemon, clock, _ = await setup(tmp_path)
        daemon.create_gate = asyncio.Event()
        await daemon.create_entered.wait()
        clock.advance(90)
        await agent.reconcile()
        pending = await agent.inspect(request.ref)
        assert pending.reason == TerminationReason.CONTROL_PLANE_LOST
        assert pending.cleanup_pending
        daemon.create_gate.set()
        final = await settled(agent, request.ref, SandboxState.TERMINATED)
        assert final.removal_confirmed and not daemon.started
        await agent.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure_point", ["stop", "removed"])
def test_transient_journal_failure_does_not_disable_unrelated_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        agent, first, daemon, clock, _ = await setup(tmp_path)
        await settled(agent, first.ref, SandboxState.READY)
        second = replace(first, ref=SandboxRef(uuid4(), 1))
        await agent.ensure(second)
        await settled(agent, second.ref, SandboxState.READY)
        clock.advance(30)
        await agent.renew_control_lease(second.ref, agent.boot_id)
        await agent.start()
        original_save = agent.journal.save
        failed = asyncio.Event()

        def fail_once() -> None:
            eligible = failure_point == "stop" or agent.records[first.ref.id].removal_confirmed
            if eligible and not failed.is_set():
                failed.set()
                raise OSError("one-time journal failure")
            original_save()

        monkeypatch.setattr(agent.journal, "save", fail_once)
        try:
            clock.advance(60)
            await asyncio.wait_for(failed.wait(), 1)
            clock.advance(31)
            first_removed = await settled(agent, first.ref, SandboxState.TERMINATED)
            second_removed = await settled(agent, second.ref, SandboxState.TERMINATED)
            assert first_removed.removal_confirmed and second_removed.removal_confirmed
            assert not daemon.resources and len(daemon.deleted_ids) == 2
            assert agent.sweeper is not None and not agent.sweeper.done()
        finally:
            monkeypatch.setattr(agent.journal, "save", original_save)
            await agent.close()

    asyncio.run(exercise())


def test_unhealthy_journal_blocks_new_execution_and_recovers_autonomously(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        agent, first, daemon, clock, _ = await setup(tmp_path)
        await settled(agent, first.ref, SandboxState.READY)
        await agent.start()
        original_save = agent.journal.save
        failed = asyncio.Event()

        def unavailable() -> None:
            failed.set()
            raise OSError("journal storage unavailable")

        monkeypatch.setattr(agent.journal, "save", unavailable)
        blocked = replace(first, ref=SandboxRef(uuid4(), 1))
        try:
            clock.advance(90)
            await asyncio.wait_for(failed.wait(), 1)
            assert hasattr(agent, "execution_ready"), "durability readiness missing"
            assert not agent.execution_ready
            with pytest.raises(BoxieError) as denied:
                await agent.ensure(blocked)
            assert denied.value.status == 503
            assert daemon.creates == 1
            assert "journal" in caplog.text
            monkeypatch.setattr(agent.journal, "save", original_save)
            removed = await settled(agent, first.ref, SandboxState.TERMINATED)
            assert removed.removal_confirmed
            for _ in range(200):
                if agent.execution_ready:
                    break
                await asyncio.sleep(0.001)
            assert agent.execution_ready
            await agent.ensure(blocked)
            await settled(agent, blocked.ref, SandboxState.READY)
            assert daemon.creates == 2
        finally:
            monkeypatch.setattr(agent.journal, "save", original_save)
            await agent.close()

    asyncio.run(exercise())


def test_failed_initial_journal_write_never_strands_an_unscheduled_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        agent, first, daemon, _, _ = await setup(tmp_path)
        await settled(agent, first.ref, SandboxState.READY)
        await agent.start()
        original_save = agent.journal.save
        broken = replace(first, ref=SandboxRef(uuid4(), 1))

        def unavailable() -> None:
            raise OSError("journal storage unavailable")

        monkeypatch.setattr(agent.journal, "save", unavailable)
        try:
            with pytest.raises(BoxieError) as failure:
                await agent.ensure(broken)
            assert failure.value.status == 503
            assert not agent.execution_ready
            assert daemon.creates == 1
        finally:
            monkeypatch.setattr(agent.journal, "save", original_save)
        try:
            removed = await settled(agent, broken.ref, SandboxState.FAILED)
            assert removed.removal_confirmed
            assert daemon.creates == 1
        finally:
            await agent.close()

    asyncio.run(exercise())


def test_journal_failure_fences_an_already_provisioning_final_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        agent, first, daemon, _, guard = await setup(tmp_path)
        await settled(agent, first.ref, SandboxState.READY)
        await agent.start()
        second = replace(first, ref=SandboxRef(uuid4(), 1))
        entered, release = asyncio.Event(), asyncio.Event()
        original_guard = guard.run_start_if_allowed

        async def delayed_guard(request: Any, boot: Any, start: Any) -> bool:
            entered.set()
            await release.wait()
            return await original_guard(request, boot, start)

        monkeypatch.setattr(guard, "run_start_if_allowed", delayed_guard)
        await agent.ensure(second)
        await asyncio.wait_for(entered.wait(), 1)
        original_save = agent.journal.save

        def unavailable() -> None:
            raise OSError("journal storage unavailable")

        monkeypatch.setattr(agent.journal, "save", unavailable)
        try:
            pending = await agent.remove(first.ref, TerminationReason.REQUESTED)
            assert pending.cleanup_pending and not pending.removal_confirmed
            assert not agent.execution_ready
            release.set()
            await asyncio.gather(agent.workers[second.ref.id], return_exceptions=True)
            assert len(daemon.started) == 1, "durability failure must fence a pending start"
        finally:
            monkeypatch.setattr(agent.journal, "save", original_save)
        try:
            await settled(agent, first.ref, SandboxState.TERMINATED)
            removed = await settled(agent, second.ref, SandboxState.FAILED)
            assert removed.removal_confirmed
        finally:
            await agent.close()

    asyncio.run(exercise())


def test_sustained_journal_outage_deletes_owned_resources_but_keeps_cleanup_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        agent, first, daemon, clock, _ = await setup(tmp_path)
        await settled(agent, first.ref, SandboxState.READY)
        daemon.create_gate = asyncio.Event()
        daemon.create_entered.clear()
        second = replace(first, ref=SandboxRef(uuid4(), 1))
        await agent.ensure(second)
        await asyncio.wait_for(daemon.create_entered.wait(), 1)
        await agent.start()
        original_save = agent.journal.save

        def unavailable() -> None:
            raise OSError("persistent journal storage outage")

        monkeypatch.setattr(agent.journal, "save", unavailable)
        try:
            clock.advance(90)
            for _ in range(200):
                if daemon.deleted_ids:
                    break
                await asyncio.sleep(0.001)
            assert len(daemon.deleted_ids) == 1, "storage failure must not defeat local expiry"
            first_pending = await agent.inspect(first.ref)
            late_pending = await agent.inspect(second.ref)
            assert first_pending.cleanup_pending and not first_pending.removal_confirmed
            assert late_pending.cleanup_pending and not late_pending.removal_confirmed
            assert not agent.execution_ready
            monkeypatch.setattr(agent.journal, "save", original_save)
            removed = await settled(agent, first.ref, SandboxState.TERMINATED)
            assert removed.removal_confirmed
            assert (await agent.inspect(second.ref)).cleanup_pending
            # A prior 404 plus recovered storage still cannot settle the unknown create.
            daemon.create_gate.set()
            late_removed = await settled(agent, second.ref, SandboxState.TERMINATED)
            assert late_removed.removal_confirmed and len(daemon.deleted_ids) == 2
            assert len(daemon.started) == 1
        finally:
            monkeypatch.setattr(agent.journal, "save", original_save)
            daemon.create_gate.set()
            await agent.close()

    asyncio.run(exercise())
