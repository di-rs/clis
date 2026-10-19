"""Supervisor execution races with controlled clocks and an external daemon boundary."""

import asyncio
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from boxie.domain.errors import BoxieError
from boxie.domain.models import Principal, SandboxState, TerminationReason
from boxie_test_support.execution import Daemon as Daemon
from boxie_test_support.execution import Guard as Guard
from boxie_test_support.execution import launch_execution
from boxie_test_support.supervisor import FakeTerminal
from lifecycle_support import settled, setup


def test_input_and_attachment_fences_and_absolute_deadline(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, clock, _ = await setup(tmp_path)

        await settled(supervisor, request.ref, SandboxState.READY)
        epoch = await supervisor.attach(request.ref, attachment_order=1)
        for _ in range(19):
            clock.advance(30)
            await supervisor.renew_control_lease(request.ref, supervisor.boot_id)
        clock.advance(29)
        await supervisor.write(request.ref, epoch, b"alive")
        clock.advance(1)
        assert supervisor._expiry(supervisor.records[request.ref.id]) is None
        await supervisor.detach(request.ref, epoch)
        for _ in range(3):
            clock.advance(30)
            await supervisor.renew_control_lease(request.ref, supervisor.boot_id)
        clock.advance(29)
        newer = await supervisor.attach(request.ref, attachment_order=2)
        assert newer > epoch
        await supervisor.detach(request.ref, epoch)
        with pytest.raises(BoxieError):
            await supervisor.write(request.ref, epoch, b"stale")
        clock.advance(1)
        assert supervisor._expiry(supervisor.records[request.ref.id]) is None
        while clock.elapsed < 3570:
            clock.advance(30)
            await supervisor.renew_control_lease(request.ref, supervisor.boot_id)
            await supervisor.write(request.ref, newer, b"active")
        clock.advance(29)
        await supervisor.write(request.ref, newer, b"last")
        clock.advance(1)
        observation = await supervisor.inspect(request.ref)
        assert observation.absolute_deadline == request.accepted_at + timedelta(seconds=3600)
        await supervisor.reconcile()
        expired = await supervisor.inspect(request.ref)
        assert expired.removal_confirmed and expired.reason == TerminationReason.EXPIRED
        assert len(daemon.deleted_ids) == 1
        await supervisor.close()

    asyncio.run(exercise())


def test_exact_ensure_boot_generation_and_snapshot_fences(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, clock, _ = await setup(tmp_path)
        from boxie.domain.lifecycle import SandboxRef

        await asyncio.gather(*(launch_execution(supervisor, request) for _ in range(10)))
        await settled(supervisor, request.ref, SandboxState.READY)
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
                await launch_execution(supervisor, bad)
        with pytest.raises(BoxieError):
            await supervisor.renew_control_lease(request.ref, uuid4())
        with pytest.raises(BoxieError):
            await supervisor.remove(SandboxRef(request.ref.id, 2), TerminationReason.REQUESTED)
        clock.advance_wall(100000)
        assert (await supervisor.inspect(request.ref)).state == SandboxState.READY
        clock.advance_monotonic(90)
        await supervisor.reconcile()
        assert (
            await supervisor.inspect(request.ref)
        ).reason == TerminationReason.CONTROL_PLANE_LOST
        await supervisor.close()

    asyncio.run(exercise())


def test_stop_during_late_create_never_confirms_early_absence(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, _, _ = await setup(tmp_path, docker_timeout=0.03)
        daemon.create_gate = asyncio.Event()
        await daemon.create_entered.wait()
        removed = await supervisor.remove(request.ref, TerminationReason.REQUESTED)
        assert removed.cleanup_pending and not removed.removal_confirmed
        await asyncio.sleep(0.06)
        await supervisor.reconcile()
        assert (await supervisor.inspect(request.ref)).cleanup_pending
        daemon.create_gate.set()
        await settled(supervisor, request.ref, SandboxState.TERMINATED)
        assert not daemon.started and len(daemon.deleted_ids) == 1
        await launch_execution(supervisor, request)
        assert daemon.creates == 1
        await supervisor.close()

    asyncio.run(exercise())


def test_lost_create_ack_and_unreachable_cleanup(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, _, _ = await setup(tmp_path)
        daemon.lost_ack = True
        ready = await settled(supervisor, request.ref, SandboxState.READY)
        assert daemon.creates == 1 and ready.container_id
        daemon.unreachable = True
        failed = await supervisor.remove(request.ref, TerminationReason.REQUESTED)
        assert failed.cleanup_pending and not failed.removal_confirmed
        daemon.unreachable = False
        await supervisor.reconcile()
        assert (await supervisor.inspect(request.ref)).removal_confirmed
        await supervisor.close()

    asyncio.run(exercise())


def test_restart_does_not_adopt_pty_or_forget_unknown_create(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, clock, guard = await setup(tmp_path)
        await settled(supervisor, request.ref, SandboxState.READY)
        from boxie.sandbox.execution.supervisor import Supervisor

        replacement = Supervisor(
            supervisor.config, daemon, supervisor.artifact_path, guard, clock, clock.monotonic
        )
        await replacement.start()
        observation = await replacement.inspect(request.ref)
        assert observation.reason == TerminationReason.SUPERVISOR_RESTART
        assert observation.removal_confirmed and observation.boot_id != supervisor.boot_id
        await launch_execution(replacement, request)
        assert daemon.creates == 1
        await replacement.close()
        await supervisor.close()

    asyncio.run(exercise())


def test_foreign_name_collision_is_never_attached_started_or_deleted(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, _, _ = await setup(tmp_path)
        from boxie.sandbox.docker import ContainerInfo

        foreign_id = str(uuid4())
        name = supervisor.records[request.ref.id].name
        daemon.resources[name] = ContainerInfo(
            id=foreign_id, labels={"boxie.installation": "foreign"}, running=False
        )
        observation = await settled(supervisor, request.ref, SandboxState.TERMINATING)
        assert observation.cleanup_pending and not observation.removal_confirmed
        assert not daemon.started and not daemon.streams and daemon.creates == 0
        assert foreign_id not in daemon.deleted_ids
        await supervisor.close()

    asyncio.run(exercise())


def test_never_attached_grace_and_lease_cannot_be_extended_by_consumer(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, _, clock, _ = await setup(tmp_path)
        ready = await settled(supervisor, request.ref, SandboxState.READY)
        clock.advance(30)
        renewed = await supervisor.renew_control_lease(request.ref, supervisor.boot_id)
        assert renewed.control_lease_until == request.accepted_at + timedelta(seconds=120)
        # Failed websocket acceptance never calls attach: original READY grace remains.
        clock.advance(89)
        assert (await supervisor.inspect(request.ref)).disconnected_at == ready.ready_at
        epoch = await supervisor.attach(request.ref, attachment_order=1)
        await supervisor.write(request.ref, epoch, b"consumer is active")
        clock.advance(1)
        await supervisor.reconcile()
        observation = await supervisor.inspect(request.ref)
        assert observation.reason == TerminationReason.CONTROL_PLANE_LOST
        assert observation.removal_confirmed
        with pytest.raises(BoxieError):
            await supervisor.renew_control_lease(request.ref, supervisor.boot_id)
        await supervisor.close()

    asyncio.run(exercise())


def test_stop_during_artifact_lookup_and_after_start_never_publishes_ready(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, _, guard = await setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()

        async def artifact(principal: Any, identifier: Any) -> Path:
            entered.set()
            await release.wait()
            return tmp_path

        supervisor.artifact_path = artifact
        await entered.wait()
        removed = await supervisor.remove(request.ref, TerminationReason.REQUESTED)
        assert removed.removal_confirmed
        release.set()
        await asyncio.sleep(0.01)
        assert daemon.creates == 0 and not daemon.started
        await supervisor.close()

        supervisor, request, daemon, _, guard = await setup(tmp_path / "after")
        original_start = daemon.start

        async def stopped_after_start(identifier: str) -> None:
            await original_start(identifier)
            guard.stopped = True

        daemon.start = stopped_after_start  # type: ignore[method-assign]
        observation = await settled(supervisor, request.ref, SandboxState.TERMINATED)
        assert observation.ready_at is None and observation.removal_confirmed
        assert len(daemon.started) == 1
        await supervisor.close()

    asyncio.run(exercise())


def test_restart_retains_unknown_create_then_reconciles_late_resource(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, clock, guard = await setup(tmp_path, docker_timeout=0.02)
        daemon.create_gate = asyncio.Event()
        await daemon.create_entered.wait()
        await supervisor.remove(request.ref, TerminationReason.REQUESTED)
        # Model process loss: preserve the daemon operation but cancel the waiting worker.
        worker = supervisor.executions[request.ref.id].activity
        assert worker is not None
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        from boxie.sandbox.execution.supervisor import Supervisor

        replacement = Supervisor(
            supervisor.config, daemon, supervisor.artifact_path, guard, clock, clock.monotonic
        )
        await replacement.start()
        assert (await replacement.inspect(request.ref)).cleanup_pending
        daemon.create_gate.set()
        creating = supervisor.executions[request.ref.id].create
        assert creating is not None
        await asyncio.wait_for(creating, 1)
        await replacement.reconcile()
        assert (await replacement.inspect(request.ref)).removal_confirmed
        assert not daemon.started
        await replacement.close()

    asyncio.run(exercise())


def test_absolute_expiry_and_stop_cannot_be_deferred_by_blocked_terminal_write(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, clock, _ = await setup(tmp_path, docker_timeout=0.02)
        ready = await settled(supervisor, request.ref, SandboxState.READY)
        epoch = await supervisor.attach(request.ref, attachment_order=1)
        entered = asyncio.Event()

        async def blocked_write(data: bytes) -> None:
            entered.set()
            await asyncio.Event().wait()

        assert ready.container_id is not None
        daemon.streams[ready.container_id].write = blocked_write  # type: ignore[method-assign]
        write = asyncio.create_task(supervisor.write(request.ref, epoch, b"blocked"))
        await entered.wait()
        stop = asyncio.create_task(supervisor.remove(request.ref, TerminationReason.REQUESTED))
        with pytest.raises(TimeoutError):
            await write
        assert (await asyncio.wait_for(stop, 0.2)).removal_confirmed
        await supervisor.close()

    asyncio.run(exercise())


def test_stop_during_attach_closes_late_stream(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, _, _ = await setup(tmp_path)
        entered, release = asyncio.Event(), asyncio.Event()
        stream = FakeTerminal()

        async def attach(identifier: str) -> FakeTerminal:
            entered.set()
            await release.wait()
            return stream

        daemon.attach = attach  # type: ignore[method-assign]
        await entered.wait()
        assert (await supervisor.remove(request.ref, TerminationReason.REQUESTED)).removal_confirmed
        release.set()
        worker = supervisor.executions[request.ref.id].activity
        assert worker is not None
        await asyncio.wait_for(worker, 1)
        assert stream.closed, "late attach must not leak a PTY after confirmed removal"
        assert not daemon.started
        await supervisor.close()

    asyncio.run(exercise())


def test_slow_cleanup_does_not_delay_another_sandbox_deadline(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, first, daemon, clock, _ = await setup(tmp_path, docker_timeout=0.2)
        from boxie.domain.lifecycle import SandboxRef

        ready = await settled(supervisor, first.ref, SandboxState.READY)
        second = replace(first, ref=SandboxRef(uuid4(), 1))
        await launch_execution(supervisor, second)
        await settled(supervisor, second.ref, SandboxState.READY)
        await supervisor.start()
        original = daemon.delete
        entered = asyncio.Event()

        async def slow_delete(identifier: str) -> None:
            if identifier == ready.container_id:
                entered.set()
                await asyncio.Event().wait()
            await original(identifier)

        daemon.delete = slow_delete  # type: ignore[method-assign]
        stop = asyncio.create_task(supervisor.remove(first.ref, TerminationReason.REQUESTED))
        await entered.wait()
        await asyncio.sleep(0.02)  # Sweeper now also sees the first pending cleanup.
        clock.advance(90)
        await asyncio.sleep(0.04)
        assert (await supervisor.inspect(second.ref)).removal_confirmed
        await stop
        await supervisor.close()

    asyncio.run(exercise())


def test_ready_grace_does_not_restart_on_retry_or_failed_acceptance(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, clock, _ = await setup(tmp_path)
        ready = await settled(supervisor, request.ref, SandboxState.READY)
        for _ in range(3):
            clock.advance(30)
            await supervisor.renew_control_lease(request.ref, supervisor.boot_id)
            await launch_execution(supervisor, request)
        # A failed handshake never invokes attach; inspect/ensure do not reset grace.
        clock.advance(30)
        await supervisor.reconcile()
        expired = await supervisor.inspect(request.ref)
        assert expired.reason == TerminationReason.DISCONNECTED
        assert expired.disconnected_at == ready.ready_at and expired.removal_confirmed
        assert len(daemon.started) == 1
        await supervisor.close()

    asyncio.run(exercise())


def test_delayed_request_uses_original_acceptance_and_approved_snapshot(tmp_path: Path) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        supervisor, request, daemon, clock, _ = await setup(tmp_path)
        await settled(supervisor, request.ref, SandboxState.READY)
        await supervisor.remove(request.ref, TerminationReason.REQUESTED)
        clock.advance(3601)
        delayed = replace(request, ref=SandboxRef(uuid4(), 1))
        await launch_execution(supervisor, delayed)
        expired = await settled(supervisor, delayed.ref, SandboxState.TERMINATED)
        assert expired.reason == TerminationReason.EXPIRED
        assert expired.absolute_deadline == request.absolute_deadline
        assert daemon.creates == 1
        await supervisor.close()

    asyncio.run(exercise())


def test_output_drain_is_active_before_start_acknowledgement(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, _, _ = await setup(tmp_path)
        started, acknowledged = asyncio.Event(), asyncio.Event()
        original = daemon.start

        async def start(identifier: str) -> None:
            await original(identifier)
            daemon.streams[identifier].queue.put_nowait(b"early output")
            started.set()
            await acknowledged.wait()

        daemon.start = start  # type: ignore[method-assign]
        await started.wait()
        terminal = supervisor.executions[request.ref.id].pty
        try:
            await asyncio.wait_for(terminal.replay.changed.wait(), 0.05)
            assert terminal.replay.after(0)[0].data == b"early output"
        finally:
            acknowledged.set()
            await supervisor.close()

    asyncio.run(exercise())


def test_control_loss_during_provisioning_prevents_late_start(tmp_path: Path) -> None:
    async def exercise() -> None:
        supervisor, request, daemon, clock, _ = await setup(tmp_path)
        daemon.create_gate = asyncio.Event()
        await daemon.create_entered.wait()
        clock.advance(90)
        await supervisor.reconcile()
        pending = await supervisor.inspect(request.ref)
        assert pending.reason == TerminationReason.CONTROL_PLANE_LOST
        assert pending.cleanup_pending
        daemon.create_gate.set()
        final = await settled(supervisor, request.ref, SandboxState.TERMINATED)
        assert final.removal_confirmed and not daemon.started
        await supervisor.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure_point", ["stop", "removed"])
def test_transient_journal_failure_does_not_disable_unrelated_expiry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_point: str
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        supervisor, first, daemon, clock, _ = await setup(tmp_path)
        await settled(supervisor, first.ref, SandboxState.READY)
        second = replace(first, ref=SandboxRef(uuid4(), 1))
        await launch_execution(supervisor, second)
        await settled(supervisor, second.ref, SandboxState.READY)
        clock.advance(30)
        await supervisor.renew_control_lease(second.ref, supervisor.boot_id)
        await supervisor.start()
        original_save = supervisor.journal.save
        failed = asyncio.Event()

        def fail_once() -> None:
            eligible = failure_point == "stop" or supervisor.records[first.ref.id].removal_confirmed
            if eligible and not failed.is_set():
                failed.set()
                raise OSError("one-time journal failure")
            original_save()

        monkeypatch.setattr(supervisor.journal, "save", fail_once)
        try:
            clock.advance(60)
            await asyncio.wait_for(failed.wait(), 1)
            clock.advance(31)
            first_removed = await settled(supervisor, first.ref, SandboxState.TERMINATED)
            second_removed = await settled(supervisor, second.ref, SandboxState.TERMINATED)
            assert first_removed.removal_confirmed and second_removed.removal_confirmed
            assert not daemon.resources and len(daemon.deleted_ids) == 2
            assert supervisor.sweeper is not None and not supervisor.sweeper.done()
        finally:
            monkeypatch.setattr(supervisor.journal, "save", original_save)
            await supervisor.close()

    asyncio.run(exercise())


def test_unhealthy_journal_blocks_new_execution_and_recovers_autonomously(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        supervisor, first, daemon, clock, _ = await setup(tmp_path)
        await settled(supervisor, first.ref, SandboxState.READY)
        await supervisor.start()
        original_save = supervisor.journal.save
        failed = asyncio.Event()

        def unavailable() -> None:
            failed.set()
            raise OSError("journal storage unavailable")

        monkeypatch.setattr(supervisor.journal, "save", unavailable)
        blocked = replace(first, ref=SandboxRef(uuid4(), 1))
        try:
            clock.advance(90)
            await asyncio.wait_for(failed.wait(), 1)
            assert hasattr(supervisor, "execution_ready"), "journal readiness missing"
            assert not supervisor.execution_ready
            with pytest.raises(BoxieError) as denied:
                await launch_execution(supervisor, blocked)
            assert denied.value.status == 503
            assert daemon.creates == 1
            assert "journal" in caplog.text
            monkeypatch.setattr(supervisor.journal, "save", original_save)
            removed = await settled(supervisor, first.ref, SandboxState.TERMINATED)
            assert removed.removal_confirmed
            for _ in range(200):
                if supervisor.execution_ready:
                    break
                await asyncio.sleep(0.001)
            assert supervisor.execution_ready
            await launch_execution(supervisor, blocked)
            await settled(supervisor, blocked.ref, SandboxState.READY)
            assert daemon.creates == 2
        finally:
            monkeypatch.setattr(supervisor.journal, "save", original_save)
            await supervisor.close()

    asyncio.run(exercise())


def test_failed_initial_journal_write_never_strands_an_unscheduled_acceptance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        supervisor, first, daemon, _, _ = await setup(tmp_path)
        await settled(supervisor, first.ref, SandboxState.READY)
        await supervisor.start()
        original_save = supervisor.journal.save
        broken = replace(first, ref=SandboxRef(uuid4(), 1))

        def unavailable() -> None:
            raise OSError("journal storage unavailable")

        monkeypatch.setattr(supervisor.journal, "save", unavailable)
        try:
            with pytest.raises(BoxieError) as failure:
                await launch_execution(supervisor, broken)
            assert failure.value.status == 503
            assert not supervisor.execution_ready
            assert daemon.creates == 1
        finally:
            monkeypatch.setattr(supervisor.journal, "save", original_save)
        try:
            removed = await settled(supervisor, broken.ref, SandboxState.FAILED)
            assert removed.removal_confirmed
            assert daemon.creates == 1
        finally:
            await supervisor.close()

    asyncio.run(exercise())


def test_journal_failure_fences_an_already_provisioning_final_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        supervisor, first, daemon, _, guard = await setup(tmp_path)
        await settled(supervisor, first.ref, SandboxState.READY)
        await supervisor.start()
        second = replace(first, ref=SandboxRef(uuid4(), 1))
        entered, release = asyncio.Event(), asyncio.Event()
        original_guard = guard.run_start_if_allowed

        async def delayed_guard(request: Any, boot: Any, start: Any) -> bool:
            entered.set()
            await release.wait()
            return await original_guard(request, boot, start)

        monkeypatch.setattr(guard, "run_start_if_allowed", delayed_guard)
        await launch_execution(supervisor, second)
        await asyncio.wait_for(entered.wait(), 1)
        original_save = supervisor.journal.save

        def unavailable() -> None:
            raise OSError("journal storage unavailable")

        monkeypatch.setattr(supervisor.journal, "save", unavailable)
        try:
            pending = await supervisor.remove(first.ref, TerminationReason.REQUESTED)
            assert pending.cleanup_pending and not pending.removal_confirmed
            assert not supervisor.execution_ready
            release.set()
            worker = supervisor.executions[second.ref.id].activity
            assert worker is not None
            await asyncio.gather(worker, return_exceptions=True)
            assert len(daemon.started) == 1, "journal failure must fence a pending start"
        finally:
            monkeypatch.setattr(supervisor.journal, "save", original_save)
        try:
            await settled(supervisor, first.ref, SandboxState.TERMINATED)
            removed = await settled(supervisor, second.ref, SandboxState.FAILED)
            assert removed.removal_confirmed
        finally:
            await supervisor.close()

    asyncio.run(exercise())


def test_sustained_journal_outage_deletes_owned_resources_but_keeps_cleanup_unconfirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        from boxie.domain.lifecycle import SandboxRef

        supervisor, first, daemon, clock, _ = await setup(tmp_path)
        await settled(supervisor, first.ref, SandboxState.READY)
        daemon.create_gate = asyncio.Event()
        daemon.create_entered.clear()
        second = replace(first, ref=SandboxRef(uuid4(), 1))
        await launch_execution(supervisor, second)
        await asyncio.wait_for(daemon.create_entered.wait(), 1)
        await supervisor.start()
        original_save = supervisor.journal.save

        def unavailable() -> None:
            raise OSError("persistent journal storage outage")

        monkeypatch.setattr(supervisor.journal, "save", unavailable)
        try:
            clock.advance(90)
            for _ in range(200):
                if daemon.deleted_ids:
                    break
                await asyncio.sleep(0.001)
            assert len(daemon.deleted_ids) == 1, "storage failure must not defeat local expiry"
            first_pending = await supervisor.inspect(first.ref)
            late_pending = await supervisor.inspect(second.ref)
            assert first_pending.cleanup_pending and not first_pending.removal_confirmed
            assert late_pending.cleanup_pending and not late_pending.removal_confirmed
            assert not supervisor.execution_ready
            monkeypatch.setattr(supervisor.journal, "save", original_save)
            removed = await settled(supervisor, first.ref, SandboxState.TERMINATED)
            assert removed.removal_confirmed
            assert (await supervisor.inspect(second.ref)).cleanup_pending
            # A prior 404 plus recovered storage still cannot settle the unknown create.
            daemon.create_gate.set()
            late_removed = await settled(supervisor, second.ref, SandboxState.TERMINATED)
            assert late_removed.removal_confirmed and len(daemon.deleted_ids) == 2
            assert len(daemon.started) == 1
        finally:
            monkeypatch.setattr(supervisor.journal, "save", original_save)
            daemon.create_gate.set()
            await supervisor.close()

    asyncio.run(exercise())


def test_definitive_create_rejection_confirms_cleanup(tmp_path: Path) -> None:
    from boxie.sandbox.docker import CreateRejected

    async def exercise() -> None:
        supervisor, request, daemon, _, _ = await setup(tmp_path)

        async def rejected(name: str, config: dict[str, Any]) -> None:
            raise CreateRejected

        daemon.create = rejected  # type: ignore[method-assign]
        try:
            failed = await settled(supervisor, request.ref, SandboxState.FAILED)
            assert failed.reason == TerminationReason.PROVISIONING_FAILED
            assert failed.removal_confirmed and not failed.cleanup_pending
            assert not supervisor.records[request.ref.id].creation_uncertain
            assert not daemon.started and not daemon.resources
        finally:
            await supervisor.close()

    asyncio.run(exercise())
