"""HTTP and WebSocket adapters for the playground service."""

import asyncio
import re
import secrets
from contextlib import AsyncExitStack, suppress
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import (
    APIRouter,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi import Path as PathParameter
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from cli_playground.boxie_client import (
    BoxieUnavailable,
    SandboxState,
    TerminationReason,
    validate_sandbox,
)
from cli_playground.models import (
    ID_PATTERN,
    SHA_PATTERN,
    ArtifactRevision,
    BuildIdentity,
    BuildUpdate,
    BuildUpdateResult,
    DeploymentResult,
    EnvironmentMetadata,
)
from cli_playground.service import CliPlaygroundService, UploadBusyError, UploadTooLargeError
from cli_playground.storage import ArtifactConflictError
from cli_playground.terminal_protocol import (
    AttachRequest,
    UpstreamAttach,
)
from cli_playground.transport import bridge_terminal, close_terminal


def create_router(service: CliPlaygroundService, static_dir: Path) -> APIRouter:
    router = APIRouter()
    settings = service.settings

    def authenticate(request: Request) -> None:
        if not secrets.compare_digest(
            request.headers.get("Authorization", ""), f"Bearer {settings.deploy_token}"
        ):
            raise HTTPException(401, "a deployment token is required")

    @router.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.post("/api/environments", status_code=201)
    async def deploy(
        request: Request,
        sha: Annotated[str, Query(pattern=f"^{SHA_PATTERN}$")],
        pr: Annotated[int, Query(gt=0)],
        run_id: Annotated[int | None, Query(gt=0)] = None,
        run_number: Annotated[int | None, Query(gt=0)] = None,
        run_attempt: Annotated[int | None, Query(gt=0)] = None,
    ) -> DeploymentResult:
        authenticate(request)
        if any(value is not None for value in (run_id, run_number, run_attempt)) and any(
            value is None for value in (run_id, run_number, run_attempt)
        ):
            raise HTTPException(422, "run_id, run_number and run_attempt must be supplied together")
        build = (
            BuildIdentity(sha=sha, run_id=run_id, run_number=run_number, run_attempt=run_attempt)
            if run_id is not None and run_number is not None and run_attempt is not None
            else None
        )
        try:
            environment = await service.deploy(request.stream(), sha, pr, build)
        except UploadBusyError as error:
            raise HTTPException(429, "another upload is in progress") from error
        except UploadTooLargeError as error:
            raise HTTPException(413, "binary bundle exceeds the upload limit") from error
        except ArtifactConflictError as error:
            raise HTTPException(409, str(error)) from error
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        except TimeoutError as error:
            raise HTTPException(408, "upload timed out") from error
        except BoxieUnavailable as error:
            raise HTTPException(503, "sandbox service unavailable") from error
        return DeploymentResult(id=environment.id, url=settings.preview_url(environment.id, pr=pr))

    @router.put("/api/pull-requests/{pr}/build")
    async def update_build(
        request: Request,
        pr: Annotated[int, PathParameter(gt=0)],
        update: BuildUpdate,
    ) -> BuildUpdateResult:
        authenticate(request)
        try:
            metadata = service.update_build(pr, update)
        except ArtifactConflictError as error:
            raise HTTPException(409, str(error)) from error
        return BuildUpdateResult(
            **metadata.model_dump(),
            url=settings.preview_url(metadata.id, pr=pr),
        )

    @router.delete("/api/pull-requests/{pr}")
    async def close_pull_request(
        request: Request, pr: Annotated[int, PathParameter(gt=0)]
    ) -> dict[str, int]:
        authenticate(request)
        return {"deleted": await service.close_pull_request(pr)}

    @router.post("/api/pull-requests/{pr}/reopen")
    async def reopen_pull_request(
        request: Request, pr: Annotated[int, PathParameter(gt=0)]
    ) -> dict[str, int]:
        authenticate(request)
        await service.reopen_pull_request(pr)
        return {"reopened": pr}

    @router.get("/api/environments/{identifier}")
    async def metadata(identifier: str) -> EnvironmentMetadata:
        result = await service.metadata(identifier)
        if result is None:
            raise HTTPException(404, "preview not found or expired")
        return result

    @router.get("/p/{identifier}")
    async def preview(identifier: str) -> FileResponse:
        if re.fullmatch(ID_PATTERN, identifier) is None:
            raise HTTPException(404, "invalid preview identifier")
        if not (static_dir / "index.html").is_file():
            raise HTTPException(503, "frontend build is missing; run pnpm build in frontend/")
        return FileResponse(static_dir / "index.html", headers={"Cache-Control": "no-store"})

    def check_origin(request: Request) -> None:
        origin = f"{urlsplit(settings.public_url).scheme}://{request.headers['host']}"
        if request.headers.getlist("origin") != [origin]:
            raise HTTPException(403, "invalid page origin")

    @router.post("/api/environments/{identifier}/sessions", status_code=201)
    async def create_session(
        request: Request, response: Response, identifier: str, body: SessionRequest
    ) -> SessionResponse:
        check_origin(request)
        try:
            binding, credential = await service.create_session(identifier, body.revision)
        except LookupError as error:
            raise HTTPException(404, "preview not found") from error
        except ArtifactConflictError as error:
            raise HTTPException(409, str(error)) from error
        except BoxieUnavailable as error:
            raise HTTPException(503, "sandbox service unavailable") from error
        response.headers["Cache-Control"] = "no-store"
        return SessionResponse(
            id=binding.id,
            preview_id=identifier,
            revision=binding.revision,
            absolute_deadline=binding.sandbox.absolute_deadline,
            state=binding.sandbox.state,
            reason=binding.sandbox.reason,
            credential=credential,
        )

    @router.delete("/api/environments/{identifier}/sessions/{session_id}")
    async def stop_session(request: Request, identifier: str, session_id: UUID) -> dict[str, str]:
        check_origin(request)
        binding = service.sessions.get(session_id)
        credentials = request.headers.getlist("authorization")
        credential = (
            credentials[0][7:]
            if len(credentials) == 1 and credentials[0].startswith("Bearer ")
            else ""
        )
        if (
            binding is None
            or binding.preview_id != identifier
            or not binding.authenticates(credential)
        ):
            raise HTTPException(404, "session not found")
        await service.stop_session(binding)
        return {"state": "terminating"}

    @router.websocket("/ws/{identifier}")
    async def terminal(
        websocket: WebSocket, identifier: str, session_id: UUID, revision: ArtifactRevision
    ) -> None:
        expected_origin = f"{urlsplit(settings.public_url).scheme}://{websocket.headers['host']}"
        if websocket.headers.getlist("origin") != [expected_origin]:
            await close_terminal(websocket, 1008)
            return
        binding = service.sessions.get(session_id)
        if binding is None or binding.preview_id != identifier or binding.revision != revision:
            await close_terminal(websocket, 1008)
            return
        await websocket.accept()
        claimed = False
        attached = False
        try:
            async with asyncio.timeout(5):
                message = await websocket.receive_text()
                if len(message.encode("utf-8")) > 1024:
                    raise ValueError("authentication frame too large")
                auth = SessionAuth.model_validate_json(message)
            binding = service.claim_attachment(identifier, session_id, revision, auth.credential)
            claimed = True
            async with AsyncExitStack() as connections:
                # One bounded transition includes readiness, connection, and acknowledgement.
                async with asyncio.timeout(40):
                    while binding.sandbox.state in {"accepted", "provisioning"}:
                        view = await service.boxie.get(binding.sandbox.id, binding.sandbox)
                        validate_sandbox(view, binding.sandbox)
                        binding.sandbox = view
                        service.binding(identifier, session_id, revision, auth.credential)
                        if view.state in {"terminating", "terminated", "failed"}:
                            raise LookupError("session ended")
                        await asyncio.sleep(0.02)
                    upstream = await connections.enter_async_context(
                        service.boxie.terminal(binding.sandbox.id)
                    )
                    service.binding(identifier, session_id, revision, auth.credential)
                    await websocket.send_json(
                        {
                            "type": "authenticated",
                            "state": "ready",
                            "absolute_deadline": binding.sandbox.absolute_deadline.isoformat().replace(
                                "+00:00", "Z"
                            ),
                            "reason": binding.sandbox.reason,
                        }
                    )

                def complete() -> None:
                    nonlocal attached
                    service.complete_attachment(
                        identifier,
                        session_id,
                        revision,
                        auth.credential,
                        binding.sandbox,
                        claim=binding.attachment_claim,
                    )
                    attached = True

                def prepare_attach(request: AttachRequest) -> str:
                    service.check_attachment_claim(
                        identifier, session_id, revision, auth.credential, binding.attachment_claim
                    )
                    # An upstream send may already be in flight when a newer claim
                    # wins. The supervisor's persisted ordering fence is authoritative.
                    return UpstreamAttach(
                        **request.model_dump(), attachment_order=binding.attachment_claim
                    ).model_dump_json()

                await bridge_terminal(websocket, upstream, complete, prepare_attach)

        except WebSocketDisconnect, asyncio.CancelledError:
            return
        except ValueError, LookupError, KeyError, TimeoutError:
            with suppress(WebSocketDisconnect, RuntimeError):
                await close_terminal(websocket, 1008)
            return
        except BoxieUnavailable:
            with suppress(WebSocketDisconnect, RuntimeError):
                await close_terminal(websocket, 1011)
            return
        finally:
            if claimed and not attached:
                # Synchronous persisted compensation also survives task cancellation.
                service.cancel_attachment(session_id, claim=binding.attachment_claim)
        with suppress(WebSocketDisconnect, RuntimeError):
            await close_terminal(websocket)

    return router


class SessionRequest(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    revision: ArtifactRevision


class SessionAuth(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    type: Literal["auth"]
    credential: str


class SessionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    preview_id: str
    revision: ArtifactRevision
    absolute_deadline: AwareDatetime
    state: SandboxState
    reason: TerminationReason | None
    credential: Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{43}$")]
