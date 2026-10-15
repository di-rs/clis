"""HTTP and WebSocket adapters for the playground service."""

import asyncio
import json
import logging
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
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from playground.boxie_client import (
    BoxieUnavailable,
    SandboxState,
    TerminalPort,
    TerminationReason,
    validate_sandbox,
)
from playground.config import Settings
from playground.models import (
    ID_PATTERN,
    SHA_PATTERN,
    ArtifactRevision,
    BuildIdentity,
    BuildUpdate,
    BuildUpdateResult,
    DeploymentResult,
    EnvironmentMetadata,
    TerminalInput,
    terminal_message,
)
from playground.service import PlaygroundService, UploadBusyError, UploadTooLargeError
from playground.storage import ArtifactConflictError

logger = logging.getLogger(__name__)
MAX_TERMINAL_OUTPUT_BYTES = 16 * 1024 * 1024


class RequestHostBoundary:
    """Accept only configured authorities; proxy headers never expand this boundary."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        hosts = Headers(scope=scope).getlist("host")
        status = 400
        valid = len(hosts) == 1 and self.settings.accepts_host(hosts[0])
        if valid and hosts[0] != urlsplit(self.settings.public_url).netloc:
            match = re.fullmatch(
                r"/(?:p|ws|api/environments)/([^/]+)(?:/sessions(?:/[0-9a-f-]+)?)?", scope["path"]
            )
            if match is not None:
                valid = self.settings.accepts_host(hosts[0], match[1])
            elif scope["path"].startswith("/api/"):
                valid = False
            if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD"}:
                valid = bool(
                    valid
                    and match
                    and (
                        (scope["method"] == "POST" and scope["path"].endswith("/sessions"))
                        or (
                            scope["method"] == "DELETE"
                            and re.fullmatch(
                                r"/api/environments/[^/]+/sessions/[0-9a-f-]+", scope["path"]
                            )
                        )
                    )
                )
            status = 404
        if not valid:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                response = PlainTextResponse("Invalid preview host", status_code=status)
                await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class TerminalOutputLimitError(Exception):
    """A session produced more output than the browser should retain."""


async def bridge_terminal(websocket: WebSocket, session: TerminalPort) -> None:
    async def output() -> None:
        forwarded = 0
        while (data := await session.receive()) is not None:
            if isinstance(data, str):
                await websocket.send_json(
                    {"type": "status", "reason": data, "message": f"Session ended: {data}."}
                )
                return
            chunk = data[: MAX_TERMINAL_OUTPUT_BYTES - forwarded]
            await websocket.send_bytes(chunk)
            forwarded += len(chunk)
            if forwarded >= MAX_TERMINAL_OUTPUT_BYTES:
                raise TerminalOutputLimitError

    async def inputs() -> None:
        while True:
            message = await websocket.receive_text()
            if len(message.encode("utf-8")) > 16384:
                raise ValueError("terminal message exceeds 16 KiB")
            data = terminal_message.validate_json(message)
            if isinstance(data, TerminalInput):
                await session.send(data.model_dump_json())
            else:
                await session.send(
                    json.dumps({"type": "resize", "columns": data.cols, "rows": data.rows})
                )

    tasks = [asyncio.create_task(output()), asyncio.create_task(inputs())]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def create_router(service: PlaygroundService, static_dir: Path) -> APIRouter:
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
            await websocket.close(code=1008)
            return
        binding = service.sessions.get(session_id)
        if binding is None or binding.preview_id != identifier or binding.revision != revision:
            await websocket.close(code=1008)
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
                    service.complete_attachment(
                        identifier, session_id, revision, auth.credential, binding.sandbox
                    )
                    attached = True
                await bridge_terminal(websocket, upstream)
        except WebSocketDisconnect, asyncio.CancelledError:
            return
        except ValueError, LookupError, KeyError, TimeoutError:
            with suppress(WebSocketDisconnect, RuntimeError):
                await websocket.close(code=1008)
            return
        except BoxieUnavailable, TerminalOutputLimitError:
            with suppress(WebSocketDisconnect, RuntimeError):
                await websocket.close(code=1011)
            return
        finally:
            if claimed and not attached:
                # Synchronous durable compensation also survives task cancellation.
                service.cancel_attachment(session_id)
        with suppress(WebSocketDisconnect, RuntimeError):
            await websocket.close(code=1000)

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
