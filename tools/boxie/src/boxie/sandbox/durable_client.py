"""Fenced HTTP control plane; only activities resolve sensitive execution requests."""

from typing import Any
from uuid import UUID

from pydantic import ValidationError

from boxie.domain.lifecycle import AgentObservation, DurableRequest, EnsureRequest, ExpiryRequest
from boxie.domain.models import AgentIdentity
from boxie.sandbox.client import AgentClient, dependency_error
from boxie.sandbox.protocol import BootWire, DurableEnsureWire, ObservationWire


class DurableAgentClient(AgentClient):
    async def __aenter__(self) -> DurableAgentClient:
        return self

    async def boot(self, expected: AgentIdentity) -> UUID:
        _status, body = await self._request("GET", "/v1/identity")
        try:
            identity = BootWire.model_validate_json(body)
            if identity.identity.domain() != expected:
                raise dependency_error()
            return identity.boot_id
        except ValidationError:
            raise dependency_error() from None

    async def _observation(
        self, path: str, body: dict[str, Any], **kwargs: Any
    ) -> AgentObservation:
        _status, response = await self._request("POST", path, json=body, **kwargs)
        try:
            return ObservationWire.model_validate_json(response).domain()
        except ValidationError, ValueError:
            raise dependency_error() from None

    async def ensure_execution(self, request: EnsureRequest, boot: UUID) -> AgentObservation:
        return await self._observation(
            f"/v1/sandboxes/{request.ref.id}/ensure",
            DurableEnsureWire.from_domain(request).model_dump(mode="json"),
            headers={"X-Agent-Boot": str(boot)},
        )

    def _execution_control(self, request: DurableRequest, boot: UUID) -> dict[str, Any]:
        return {
            **self._control(request.principal, request.identity, request.generation),
            "boot_id": str(boot),
            "reason": (request.reason or "requested"),
        }

    async def inspect_execution(self, request: DurableRequest, boot: UUID) -> AgentObservation:
        return await self._observation(
            f"/v1/sandboxes/{request.sandbox_id}/inspect", self._execution_control(request, boot)
        )

    async def renew_execution(self, request: DurableRequest, boot: UUID) -> AgentObservation:
        return await self._observation(
            f"/v1/sandboxes/{request.sandbox_id}/renew", self._execution_control(request, boot)
        )

    async def remove_execution(self, request: DurableRequest, boot: UUID) -> AgentObservation:
        return await self._observation(
            f"/v1/sandboxes/{request.sandbox_id}/remove", self._execution_control(request, boot)
        )

    async def expire_execution(
        self, request: DurableRequest, expiry: ExpiryRequest
    ) -> AgentObservation:
        return await self._observation(
            f"/v1/sandboxes/{request.sandbox_id}/expire",
            {
                **self._execution_control(request, expiry.boot_id),
                "reason": expiry.reason.value,
                "observed_event_version": expiry.observed_event_version,
            },
        )
