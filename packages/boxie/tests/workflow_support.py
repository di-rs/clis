"""Controlled external activity decisions for workflow and cancellation tests."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError


class Decisions:
    def __init__(self, mode: str) -> None:
        from boxie.temporal.contracts import LifecycleFacts

        self.mode = mode
        self.facts = LifecycleFacts(
            absolute_deadline=(datetime.now(UTC) + timedelta(hours=1)).timestamp(),
            desired_stop=mode == "stop_first",
            cleaned=False,
        )
        self.ensures = self.removals = self.inspections = self.stops = 0
        self.stop_committed = False
        self.removal_attempts: list[int] = []
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        if mode == "cancel_expire":
            self.facts = self.facts.model_copy(update={"absolute_deadline": 0})
        if mode in {"idle", "detached", "absolute"}:
            seconds = {"idle": 600, "detached": 120, "absolute": 3600}[mode]
            self.facts = self.facts.model_copy(
                update={"next_deadline": self.facts.absolute_deadline - 3600 + seconds}
            )

    @activity.defn(name="load_request")
    async def load(self, sandbox_id: str) -> Any:
        if self.mode == "cancel_load":
            self.entered.set()
            await self.release.wait()
        return self.facts

    @activity.defn(name="ensure_sandbox")
    async def ensure(self, sandbox_id: str) -> None:
        self.ensures += 1
        if self.mode == "cancel_ensure":
            self.entered.set()
            await self.release.wait()
        if self.mode == "unsafe_failure":
            try:
                raise RuntimeError("SECRET-NESTED-CONTEXT")
            except RuntimeError as error:
                raise RuntimeError("SECRET-ENV-HTTP-ERROR") from error
        if self.mode in {"idle", "detached", "absolute", "cancel_inspect", "cancel_expire"}:
            return
        if self.mode == "provision_failure":
            raise ApplicationError("safe failure", type="DependencyUnavailable")
        self.facts = self.facts.model_copy(update={"desired_stop": True})

    @activity.defn(name="inspect_and_renew")
    async def inspect(self, sandbox_id: str) -> Any:
        self.inspections += 1
        if self.mode == "cancel_inspect":
            self.entered.set()
            await self.release.wait()
        return self.facts

    @activity.defn(name="expire_if_current")
    async def expire(self, sandbox_id: str) -> Any:
        if self.mode == "cancel_expire":
            self.entered.set()
            await self.release.wait()
        self.facts = self.facts.model_copy(update={"desired_stop": True})
        return self.facts

    @activity.defn(name="request_stop")
    async def stop(self, request: dict[str, str]) -> None:
        self.stops += 1
        if self.mode == "cancel_expire" and self.stops == 1:
            self.entered.set()
            await self.release.wait()
        if self.mode.startswith("terminal_stop_") and self.stops == 1:
            self.cleanup_fault()
        self.stop_committed = True
        self.facts = self.facts.model_copy(update={"desired_stop": True})

    @activity.defn(name="remove_sandbox")
    async def remove(self, sandbox_id: str) -> None:
        assert self.stop_committed, "removal must follow SQL stop commit"
        self.removals += 1
        self.removal_attempts.append(activity.info().attempt)
        if self.mode.startswith("terminal_remove_") and self.removals == 1:
            self.cleanup_fault()
        if self.mode == "cancel_cleanup":
            self.entered.set()
            await self.release.wait()
        if self.mode == "native_retry" and self.removals <= 100:
            raise ApplicationError("pending", type="CleanupPending")
        self.facts = self.facts.model_copy(update={"cleaned": True})

    def cleanup_fault(self) -> None:
        from boxie.domain.errors import BoxieError

        fault = self.mode.rsplit("_", 1)[1]
        if fault == "application":
            raise ApplicationError(
                "SECRET-CLEANUP-DETAIL", type="InvalidRequest", non_retryable=True
            )
        raise BoxieError(int(fault), "SECRET-CLEANUP-DETAIL")

    @activity.defn(name="record_observation")
    async def record(self, sandbox_id: str) -> None:
        pass

    @activity.defn(name="confirm_removed")
    async def confirm(self, sandbox_id: str) -> bool:
        return self.facts.cleaned

    def activities(self) -> list[Any]:
        return [
            self.load,
            self.ensure,
            self.inspect,
            self.expire,
            self.stop,
            self.remove,
            self.record,
            self.confirm,
        ]
