"""Bound artifact transport and publication against captured lifecycle generations."""

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from time import sleep
from typing import Protocol

from ci.github import GitHub, array, table

from playground.artifacts import MAX_ARCHIVE, binary_archive

from .github import WORKFLOW, current_pull, positive

TIMEOUT = 60
ARTIFACT_NAME = "cli-playground-binaries"
MARKER = re.compile(r"<!-- cli-playground run=(\d+) attempt=(\d+) -->")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        raise ValueError("playground request redirect refused")


def public_origin(value: str) -> str:
    value = value.rstrip("/")
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?", value):
        raise ValueError("PLAYGROUND_URL must be an HTTPS origin")
    return value


def gateway_request(
    base: str,
    token: str,
    path: str,
    *,
    method: str,
    data: bytes | None = None,
    content_type: str = "application/zip",
) -> dict[str, object]:
    request = urllib.request.Request(
        base + path,
        data=data,
        headers={"Authorization": "Bearer " + token, "Content-Type": content_type},
        method=method,
    )
    with urllib.request.build_opener(NoRedirect).open(request, timeout=TIMEOUT) as response:
        payload = response.read(16385)
    if len(payload) > 16384:
        raise ValueError("playground response exceeds size limit")
    return table(json.loads(payload))


class GatewayRequest(Protocol):
    def __call__(
        self,
        base: str,
        token: str,
        path: str,
        *,
        method: str,
        data: bytes | None = None,
        content_type: str = "application/zip",
    ) -> dict[str, object]: ...


def lifecycle_state(base: str, token: str, pr: int, gateway: GatewayRequest) -> tuple[int, bool]:
    state = gateway(base, token, f"/api/pull-requests/{pr}/lifecycle", method="GET")
    generation, closed = state.get("generation"), state.get("closed")
    if type(generation) is not int or not 0 <= generation <= 10**18 or type(closed) is not bool:
        raise ValueError("invalid lifecycle response")
    return generation, closed


def lifecycle_matches(
    base: str, token: str, pr: int, expected: int, gateway: GatewayRequest
) -> bool:
    return lifecycle_state(base, token, pr, gateway) == (expected, False)


def upload(
    api: GitHub,
    repo: str,
    base: str,
    token: str,
    sha: str,
    pr: int,
    data: bytes,
    *,
    run_id: int,
    run_number: int,
    run_attempt: int,
    expected_generation: int,
    gateway: GatewayRequest = gateway_request,
    sleep: Callable[[float], None] = sleep,
) -> dict[str, object] | None:
    query = urllib.parse.urlencode(
        {
            "sha": sha,
            "pr": pr,
            "run_id": run_id,
            "run_number": run_number,
            "run_attempt": run_attempt,
            "expected_generation": expected_generation,
        }
    )
    # Two minutes of backoff allow for the gateway's 60-second upload plus cleanup.
    delays = iter((2, 4, 8, 16, 30, 30, 30))
    while True:
        if not current_pull(api, repo, pr, sha) or not lifecycle_matches(
            base, token, pr, expected_generation, gateway
        ):
            return None
        try:
            response = gateway(base, token, "/api/environments?" + query, method="POST", data=data)
            if (
                type(response.get("expected_generation")) is not int
                or response["expected_generation"] != expected_generation
                or not lifecycle_matches(base, token, pr, expected_generation, gateway)
            ):
                return None
            return response
        except urllib.error.HTTPError as error:
            if error.code == 409 and (stale_build(error) or not current_pull(api, repo, pr, sha)):
                return None
            delay = next(delays, None)
            if error.code != 429 or delay is None:
                raise
            error.close()
            sleep(delay)


def stale_build(error: urllib.error.HTTPError) -> bool:
    try:
        payload = error.read(16385)
        return len(payload) <= 16384 and table(json.loads(payload)).get("detail") in {
            "stale build",
            "stale lifecycle",
        }
    except ValueError:
        return False
    finally:
        error.close()


def update_build(
    api: GitHub,
    repo: str,
    base: str,
    token: str,
    sha: str,
    pr: int,
    run_id: int,
    revision: tuple[int, int],
    state: str,
    *,
    expected_generation: int,
    gateway: GatewayRequest = gateway_request,
) -> dict[str, object] | None:
    if not current_pull(api, repo, pr, sha) or not lifecycle_matches(
        base, token, pr, expected_generation, gateway
    ):
        return None
    try:
        response = gateway(
            base,
            token,
            f"/api/pull-requests/{pr}/build?expected_generation={expected_generation}",
            method="PUT",
            content_type="application/json",
            data=json.dumps(
                {
                    "sha": sha,
                    "run_id": run_id,
                    "run_number": revision[0],
                    "run_attempt": revision[1],
                    "state": state,
                }
            ).encode(),
        )
        if (
            type(response.get("expected_generation")) is not int
            or response["expected_generation"] != expected_generation
            or not lifecycle_matches(base, token, pr, expected_generation, gateway)
        ):
            return None
        return response
    except urllib.error.HTTPError as error:
        if error.code == 409 and (stale_build(error) or not current_pull(api, repo, pr, sha)):
            return None
        raise


def preview_url(base: str, response: dict[str, object], pr: int) -> str:
    identifier = response.get("id")
    if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", identifier):
        raise ValueError("invalid preview identifier")
    expected = base + "/p/" + identifier
    if response.get("url") == expected:
        return expected
    origin = urllib.parse.urlsplit(public_origin(base))
    expected = f"https://pr-{pr}.{origin.netloc}/p/{identifier}"
    if response.get("url") == expected:
        return expected
    if re.fullmatch(r"[0-9a-f]{32}", identifier):
        expected = f"https://{identifier}.{origin.netloc}/p/{identifier}"
        if response.get("url") == expected:
            return expected
    raise ValueError("invalid preview URL")


def publish(
    api: GitHub,
    repo: str,
    run_id: int,
    base_url: str,
    token: str,
    *,
    gateway: GatewayRequest = gateway_request,
    sleep: Callable[[float], None] = sleep,
) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    base = f"repos/{repo}"
    run = table(api.json(f"{base}/actions/runs/{positive(run_id)}"))
    if (
        run.get("id") != run_id
        or run.get("event") != "pull_request"
        or run.get("status")
        not in {"queued", "in_progress", "completed", "waiting", "pending", "requested"}
        or run.get("path") != WORKFLOW
        or run.get("name") != "🏗️ Playground PR binary builds"
        or table(run.get("repository")).get("full_name") != repo
    ):
        return None
    workflow = table(api.json(f"{base}/actions/workflows/cli-playground.yml"))
    if workflow.get("id") != run.get("workflow_id") or workflow.get("path") != WORKFLOW:
        return None
    sha = run.get("head_sha")
    if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("invalid commit SHA")
    pulls = [table(item) for item in array(run.get("pull_requests", []))]
    if not pulls:
        pulls = api.pages(f"{base}/commits/{sha}/pulls")
    matches = [
        positive(item.get("number"))
        for item in pulls
        if current_pull(api, repo, positive(item.get("number")), sha)
    ]
    if len(matches) != 1:
        return None
    number = matches[0]
    revision = (positive(run.get("run_number")), positive(run.get("run_attempt")))
    comments = api.pages(f"{base}/issues/{number}/comments")
    owned = [
        item
        for item in comments
        if table(item.get("user")).get("login") == "github-actions[bot]"
        and MARKER.match(str(item.get("body", "")))
    ]
    for item in owned:
        marker = MARKER.match(str(item["body"]))
        assert marker is not None
        if (int(marker[1]), int(marker[2])) > revision:
            return None
    configured = bool(base_url and token)
    generation = 0
    if configured:
        base_url = public_origin(base_url)
        generation, closed = lifecycle_state(base_url, token, number, gateway)
        if closed:
            return None
    run_url = f"https://github.com/{repo}/actions/runs/{run_id}"
    paragraphs = [
        f"<!-- cli-playground run={revision[0]} attempt={revision[1]} -->\n### CLI playground",
        f"Commit `{sha[:12]}` · [Build and artifacts]({run_url})",
    ]
    if not configured:
        paragraphs.append(
            "**Configuration needed:** set repository variable `PLAYGROUND_URL` and secret "
            "`PLAYGROUND_DEPLOY_TOKEN` after deploying the gateway. "
            "Live previews are unavailable; binaries can be downloaded below."
        )
    completed = run.get("status") == "completed"
    state = (
        "building"
        if not completed
        else "cancelled"
        if run.get("conclusion") == "cancelled"
        else "failed"
    )
    if not completed:
        paragraphs.append("**Building:** this revision is not ready yet.")
    elif state == "cancelled":
        paragraphs.append("**Build cancelled:** no new live preview was published.")
    elif run.get("conclusion") != "success":
        paragraphs.append(
            "**Build failed:** no new live previews were published. Check the build logs."
        )
    artifacts = (
        api.pages(f"{base}/actions/runs/{run_id}/artifacts", "artifacts") if completed else []
    )
    artifact = max(
        (
            item
            for item in artifacts
            if item.get("name") == ARTIFACT_NAME and not item.get("expired")
        ),
        key=lambda item: positive(item.get("id")),
        default=None,
    )
    publish_failed = False
    url: str | None = None
    if artifact is None:
        expired = any(
            item.get("name") == ARTIFACT_NAME and item.get("expired") for item in artifacts
        )
        if expired:
            paragraphs.append("**Artifacts expired; rerun build** to restore this preview.")
        elif completed:
            paragraphs.append("No CLI binary artifacts were produced for this run.")
        if completed and run.get("conclusion") == "success":
            state = "failed" if expired else "empty"
    else:
        artifact_id = positive(artifact.get("id"))
        paragraphs.append(f"[Download binaries]({run_url}/artifacts/{artifact_id})")
        if configured and run.get("conclusion") == "success":
            try:
                positive(artifact.get("size_in_bytes"), MAX_ARCHIVE)
                associated = table(artifact.get("workflow_run"))
                if associated.get("id") != run_id or associated.get("head_sha") != sha:
                    raise ValueError("artifact belongs to another run")
                if revision[1] > 1 and (
                    not isinstance(artifact.get("created_at"), str)
                    or not isinstance(run.get("run_started_at"), str)
                    or str(artifact["created_at"]) < str(run["run_started_at"])
                ):
                    raise ValueError("artifact belongs to an earlier run attempt")
                bundle = api.request(
                    f"{base}/actions/artifacts/{artifact_id}/zip", limit=MAX_ARCHIVE
                )
                count = len(binary_archive(bundle))
                response = upload(
                    api,
                    repo,
                    base_url,
                    token,
                    sha,
                    number,
                    bundle,
                    run_id=run_id,
                    run_number=revision[0],
                    run_attempt=revision[1],
                    expected_generation=generation,
                    gateway=gateway,
                    sleep=sleep,
                )
                if response is None:
                    return None
                url = preview_url(base_url, response, number)
                paragraphs.append(f"[Open playground]({url}) · {count} CLIs in one shared shell.")
            except OSError, ValueError, KeyError, zipfile.BadZipFile, subprocess.SubprocessError:
                publish_failed = True
                paragraphs.append(
                    "Publish failed: check the binary bundle and gateway configuration."
                )
    if configured and not url:
        status_response = update_build(
            api,
            repo,
            base_url,
            token,
            sha,
            number,
            run_id,
            revision,
            state,
            expected_generation=generation,
            gateway=gateway,
        )
        if status_response is None:
            return None
        if (
            status_response.get("build") is not None
            and table(status_response["build"]).get("state") != state
        ):
            # Another publisher may have completed the same run while this event waited.
            return None
        status_url = preview_url(base_url, status_response, number)
        paragraphs.append(f"[View preview status]({status_url})")
    if not current_pull(api, repo, number, sha) or (
        configured and not lifecycle_matches(base_url, token, number, generation, gateway)
    ):
        return None
    if url:
        deployment = table(
            api.json(
                f"{base}/deployments",
                method="POST",
                body={
                    "ref": sha,
                    "auto_merge": False,
                    "required_contexts": [],
                    "environment": f"cli-playground/pr-{number}",
                    "description": f"CLI playground for PR #{number}",
                    "transient_environment": True,
                    "production_environment": False,
                },
            )
        )
        if not current_pull(api, repo, number, sha) or not lifecycle_matches(
            base_url, token, number, generation, gateway
        ):
            return None
        api.request(
            f"{base}/deployments/{positive(deployment.get('id'))}/statuses",
            method="POST",
            body={
                "state": "success",
                "environment_url": url,
                "log_url": run_url,
                "description": "All changed CLIs; preview expires after seven days",
                "auto_inactive": True,
            },
        )
    paragraphs.append(
        "Live previews expire seven days after upload. Each terminal starts with a fresh filesystem."
    )
    markdown = "\n\n".join(paragraphs) + "\n"
    if not current_pull(api, repo, number, sha) or (
        configured and not lifecycle_matches(base_url, token, number, generation, gateway)
    ):
        return None
    comment_path = (
        f"issues/comments/{positive(owned[0].get('id'))}" if owned else f"issues/{number}/comments"
    )
    api.request(
        f"{base}/{comment_path}", method="PATCH" if owned else "POST", body={"body": markdown}
    )
    if publish_failed:
        raise ValueError("Failed to publish the CLI preview")
    return markdown
