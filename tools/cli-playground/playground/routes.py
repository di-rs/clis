"""HTTP and WebSocket adapters for the playground service."""

import asyncio
import logging
import re
import secrets
from contextlib import suppress
from pathlib import Path
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi import Path as PathParameter
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

from playground.config import Settings
from playground.models import (
    ARTIFACT_REVISION_PATTERN,
    ID_PATTERN,
    SHA_PATTERN,
    BuildIdentity,
    BuildUpdate,
    BuildUpdateResult,
    DeploymentResult,
    EnvironmentMetadata,
    TerminalInput,
    terminal_message,
)
from playground.service import PlaygroundService, UploadBusyError, UploadTooLargeError
from playground.sessions import DockerSession, SessionLimitError
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
            match = re.fullmatch(r"/(?:p|ws|api/environments)/([^/]+)", scope["path"])
            if match is not None:
                valid = self.settings.accepts_host(hosts[0], match[1])
            elif scope["path"].startswith("/api/"):
                valid = False
            if scope["type"] == "http" and scope["method"] not in {"GET", "HEAD"}:
                valid = False
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


async def bridge_terminal(websocket: WebSocket, session: DockerSession) -> None:
    async def output() -> None:
        forwarded = 0
        while data := await session.read():
            chunk = data[: MAX_TERMINAL_OUTPUT_BYTES - forwarded]
            await websocket.send_bytes(chunk)
            forwarded += len(chunk)
            if forwarded >= MAX_TERMINAL_OUTPUT_BYTES:
                raise TerminalOutputLimitError

    async def inputs() -> None:
        while True:
            message = await websocket.receive_text()
            if len(message) > 16384:
                raise ValueError("terminal message exceeds 16 KiB")
            data = terminal_message.validate_json(message)
            if isinstance(data, TerminalInput):
                await session.write(data.data.encode("utf-8"))
            else:
                await session.resize(data.cols, data.rows)

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

    @router.websocket("/ws/{identifier}")
    async def terminal(
        websocket: WebSocket,
        identifier: str,
        sha: Annotated[str, Query(pattern=f"^{SHA_PATTERN}$")],
        revision: Annotated[str | None, Query(pattern=f"^{ARTIFACT_REVISION_PATTERN}$")] = None,
    ) -> None:
        expected_origin = f"{urlsplit(settings.public_url).scheme}://{websocket.headers['host']}"
        if websocket.headers.getlist("origin") != [expected_origin]:
            await websocket.close(code=1008)
            return
        environment = service.store.get(identifier)
        if (
            environment is None
            or environment.sha != sha
            or (environment.revision != (revision or sha))
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        # Accept yields to cleanup/PR-close requests. Recheck before registering a session.
        environment = service.store.get(identifier)
        if (
            environment is None
            or environment.sha != sha
            or (environment.revision != (revision or sha))
        ):
            await websocket.close(code=1008)
            return
        message = "Session ended. Reset to start with fresh files."
        code = 1000
        try:
            async with service.sessions.open(environment) as session:
                await bridge_terminal(websocket, session)
        except WebSocketDisconnect, asyncio.CancelledError:
            return
        except SessionLimitError as error:
            message, code = str(error), 1013
        except TimeoutError:
            message = "The 15-minute session expired. Reset to start again."
        except TerminalOutputLimitError:
            message = "Terminal output limit reached. Redirect large output to a file."
        except ValueError, KeyError:
            message, code = "Invalid terminal input.", 1008
        except OSError, RuntimeError:
            logger.exception("Playground session failed")
            message, code = "The sandbox could not start. Please try again later.", 1011
        finally:
            service.collect_artifacts()
        with suppress(WebSocketDisconnect, RuntimeError):
            await websocket.send_json({"type": "status", "message": message})
            await websocket.close(code=code)

    return router
