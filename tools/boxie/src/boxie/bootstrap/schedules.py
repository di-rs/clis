"""Verify the native recovery/maintenance schedules before admitting new work."""

from datetime import timedelta

from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)

from boxie.domain.models import OrchestratorIdentity
from boxie.temporal.contracts import maintenance_queue
from boxie.temporal.maintenance import Maintenance


def schedule_definitions(identity: OrchestratorIdentity) -> dict[str, Schedule]:
    queue = maintenance_queue(identity)
    return {
        f"{queue}/{kind}": Schedule(
            action=ScheduleActionStartWorkflow(
                Maintenance.run, kind, id=f"{queue}/{kind}", task_queue=queue
            ),
            spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=seconds))]),
            policy=SchedulePolicy(
                overlap=ScheduleOverlapPolicy.SKIP, catchup_window=timedelta(minutes=1)
            ),
        )
        for kind, seconds in (
            ("commands", 5),
            ("artifacts", 30),
            ("journal", 30),
            ("retention", 30),
        )
    }


async def check_schedules(client: Client, identity: OrchestratorIdentity) -> None:
    for identifier, expected in schedule_definitions(identity).items():
        actual = (await client.get_schedule_handle(identifier).describe()).schedule
        action, wanted = actual.action, expected.action
        if not isinstance(action, ScheduleActionStartWorkflow) or not isinstance(
            wanted, ScheduleActionStartWorkflow
        ):
            raise RuntimeError("maintenance schedule action differs")
        args = await client.data_converter.decode(list(action.args))
        if (
            actual.state.paused
            or actual.state.limited_actions
            or actual.spec != expected.spec
            or (action.workflow, action.id, action.task_queue, args)
            != (wanted.workflow, wanted.id, wanted.task_queue, list(wanted.args))
            or actual.policy != expected.policy
        ):
            raise RuntimeError("maintenance schedule configuration differs")


async def ensure_schedules(client: Client, identity: OrchestratorIdentity) -> None:
    for identifier, schedule in schedule_definitions(identity).items():
        try:
            await client.create_schedule(identifier, schedule)
        except ScheduleAlreadyRunningError:
            pass
    await check_schedules(client, identity)
