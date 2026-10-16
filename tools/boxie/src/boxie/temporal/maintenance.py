"""Bounded maintenance operations scheduled by Temporal, with no process polling."""

from datetime import timedelta

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

with workflow.unsafe.imports_passed_through():
    from boxie.persistence.postgres import PostgresLifecycle
    from boxie.persistence.postgres_artifacts import PostgresArtifactStore
    from boxie.sandbox.execution.orchestrator import Orchestrator
    from boxie.temporal.dispatcher import CommandDelivery


class MaintenanceActivities:
    def __init__(
        self,
        delivery: CommandDelivery,
        repository: PostgresLifecycle,
        orchestrator: Orchestrator,
        artifacts: PostgresArtifactStore,
    ) -> None:
        self.delivery, self.repository = delivery, repository
        self.orchestrator, self.artifacts = orchestrator, artifacts

    @activity.defn
    async def maintain(self, kind: str) -> None:
        if kind == "commands":
            await self.delivery.recover_once()
        elif kind == "artifacts":
            await self.artifacts.collect()
            await self.artifacts.collect_orphans()
        elif kind == "journal":
            await self.orchestrator.compact()
        elif kind == "retention":
            await self.repository.purge_retained()
        else:
            raise ValueError("unknown maintenance operation")


@workflow.defn
class Maintenance:
    @workflow.run
    async def run(self, kind: str) -> None:
        await workflow.execute_activity_method(
            MaintenanceActivities.maintain,
            kind,
            start_to_close_timeout=timedelta(seconds=20),
            schedule_to_close_timeout=timedelta(seconds=20 if kind == "commands" else 60),
            retry_policy=RetryPolicy(maximum_interval=timedelta(seconds=10)),
        )
