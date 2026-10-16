#!/usr/bin/env python3.15
"""Build PR binaries without secrets; publish bounded artifacts from trusted code."""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from time import sleep
from typing import Protocol

# Keep direct script invocation usable from any working directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from ci.github import GitHub, PaginationLimit, array, table  # noqa: E402
from playground.artifacts import CLI_PATTERN, MAX_ARCHIVE, MAX_CLIS, binary_archive  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ".github/workflows/cli-playground.yml"
TIMEOUT = 60
ARTIFACT_NAME = "cli-playground-binaries"
MARKER = re.compile(r"<!-- cli-playground run=(\d+) attempt=(\d+) -->")


def positive(value: object, maximum: int = 10**18) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("invalid integer")
    return value


def discover(root: Path = ROOT) -> dict[str, str]:
    manifest = tomllib.loads((root / "Cargo.toml").read_text())
    workspace = manifest["workspace"]
    excluded = {path for pattern in workspace.get("exclude", []) for path in root.glob(pattern)}
    members = {path for pattern in workspace["members"] for path in root.glob(pattern)}
    packages: dict[str, str] = {}
    for directory in sorted(members - excluded):
        data = tomllib.loads((directory / "Cargo.toml").read_text())
        if not (directory / "src/main.rs").is_file():
            continue
        name = data["package"]["name"]
        binaries = data.get("bin", [])
        if (
            not isinstance(name, str)
            or not re.fullmatch(CLI_PATTERN, name)
            or name != directory.name
            or name in packages
            or any((directory / "src/bin").glob("**/*.rs"))
            or len(binaries) > 1
            or binaries
            and (
                binaries[0].get("name") != name
                or binaries[0].get("path", "src/main.rs") != "src/main.rs"
            )
        ):
            raise ValueError("CLI package must define exactly its matching binary")
        packages[name] = directory.relative_to(root).as_posix()
    if not packages or len(packages) > MAX_CLIS:
        raise ValueError("unexpected CLI package count")
    return packages


def production(path: str, directory: str) -> bool:
    prefix = directory + "/"
    if not path.startswith(prefix):
        return False
    relative = path.removeprefix(prefix)
    return relative in {"Cargo.toml", "build.rs"} or relative.startswith("src/")


def select(packages: dict[str, str], paths: list[str]) -> list[str]:
    paths = [
        path
        for path in paths
        if not path.endswith(".md") and not {"tests", "docs"}.intersection(Path(path).parts)
    ]
    global_paths = {
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "tools/cli-playground/Build.Dockerfile",
        "tools/cli-playground/Build.Dockerfile.dockerignore",
        "tools/cli-playground/preview_ci.py",
        WORKFLOW,
    }
    if any(
        path in global_paths
        or path.startswith(".cargo/")
        or path.startswith("utils/")
        and "/src/" in path
        or path.startswith("utils/")
        and path.endswith(("/Cargo.toml", "/build.rs"))
        for path in paths
    ):
        return sorted(packages)
    return sorted(
        name
        for name, directory in packages.items()
        if any(production(path, directory) for path in paths)
    )


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
        }
    )
    # Two minutes of backoff allow for the gateway's 60-second upload plus cleanup.
    delays = iter((2, 4, 8, 16, 30, 30, 30))
    while True:
        if not current_pull(api, repo, pr, sha):
            return None
        try:
            return gateway(base, token, "/api/environments?" + query, method="POST", data=data)
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
        return len(payload) <= 16384 and table(json.loads(payload)).get("detail") == "stale build"
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
    gateway: GatewayRequest = gateway_request,
) -> dict[str, object] | None:
    if not current_pull(api, repo, pr, sha):
        return None
    try:
        return gateway(
            base,
            token,
            f"/api/pull-requests/{pr}/build",
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


def current_pull(
    api: GitHub, repo: str, number: int, sha: str | None = None, *, state: str = "open"
) -> bool:
    pull = table(api.json(f"repos/{repo}/pulls/{number}"))
    base = table(pull.get("base"))
    return (
        pull.get("number") == number
        and pull.get("state") == state
        and (sha is None or table(pull.get("head")).get("sha") == sha)
        and base.get("ref") == "master"
        and table(base.get("repo")).get("full_name") == repo
    )


def lifecycle(
    api: GitHub,
    repo: str,
    number: int,
    action: str,
    base_url: str,
    token: str,
    *,
    gateway: GatewayRequest = gateway_request,
) -> str | None:
    """Reconcile trusted close/reopen events with the PR's current GitHub state."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("invalid repository")
    positive(number)
    if action not in {"closed", "reopened"}:
        raise ValueError("invalid lifecycle action")
    state = "closed" if action == "closed" else "open"
    if not current_pull(api, repo, number, state=state):
        return None
    if not base_url or not token:
        return (
            "Configuration needed: PLAYGROUND_URL and PLAYGROUND_DEPLOY_TOKEN. "
            "No hosted environments were cleaned or reopened."
        )
    base_url = public_origin(base_url)
    path = f"/api/pull-requests/{number}"
    if action == "reopened":
        response = gateway(base_url, token, path + "/reopen", method="POST")
        if positive(response.get("reopened")) != number:
            raise ValueError("invalid reopen acknowledgement")
        # A build can finish before the lifecycle job clears the closed tombstone.
        # Replay its current state through the same trusted publisher after reopening.
        pull = table(api.json(f"repos/{repo}/pulls/{number}"))
        sha = table(pull.get("head")).get("sha")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("invalid commit SHA")
        query = urllib.parse.urlencode({"event": "pull_request", "head_sha": sha})
        runs = api.pages(
            f"repos/{repo}/actions/workflows/cli-playground.yml/runs?{query}", "workflow_runs"
        )
        latest = max(
            (
                run
                for run in runs
                if run.get("head_sha") == sha
                and run.get("path") == WORKFLOW
                and run.get("event") == "pull_request"
            ),
            key=lambda run: (positive(run.get("run_number")), positive(run.get("run_attempt"))),
            default=None,
        )
        if latest is not None:
            publish(api, repo, positive(latest.get("id")), base_url, token, gateway=gateway)
        return f"PR #{number} reopened; reconciled the latest available build for its current head."
    response = gateway(base_url, token, path, method="DELETE")
    deleted = response.get("deleted")
    if type(deleted) is not int or deleted < 0:
        raise ValueError("invalid cleanup acknowledgement")
    message = f"Deleted {deleted} hosted playground environment(s) for closed PR #{number}."
    try:
        query = urllib.parse.urlencode({"environment": f"cli-playground/pr-{number}"})
        deployments = api.pages(f"repos/{repo}/deployments?{query}")
    except PaginationLimit:
        return (
            message + " GitHub deployment status listing reached its pagination limit; "
            "some GitHub statuses may remain active."
        )
    for deployment in deployments:
        if deployment.get("environment") != f"cli-playground/pr-{number}":
            continue
        # Reopening can race with cleanup; its serialized event clears the tombstone.
        if not current_pull(api, repo, number, state="closed"):
            break
        api.request(
            f"repos/{repo}/deployments/{positive(deployment.get('id'))}/statuses",
            method="POST",
            body={
                "state": "inactive",
                "description": "Pull request closed",
                "auto_inactive": False,
            },
        )
    return message


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
        or run.get("name") != "CLI playground build"
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
    if configured:
        base_url = public_origin(base_url)
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
            api, repo, base_url, token, sha, number, run_id, revision, state, gateway=gateway
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
    if not current_pull(api, repo, number, sha):
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
    if not current_pull(api, repo, number, sha):
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("--base", required=True)
    build.add_argument("--head", required=True)
    build.add_argument("--output", required=True, type=Path)
    publication = commands.add_parser("publish")
    publication.add_argument("--repo", required=True)
    publication.add_argument("--run-id", required=True, type=int)
    lifecycle_command = commands.add_parser("lifecycle")
    lifecycle_command.add_argument("--repo", required=True)
    lifecycle_command.add_argument("--pr", required=True, type=int)
    lifecycle_command.add_argument("--action", required=True, choices=("closed", "reopened"))
    args = parser.parse_args()
    try:
        if args.command == "build":
            diff = subprocess.run(
                [
                    "git",
                    "diff",
                    "--name-only",
                    "--no-renames",
                    "-z",
                    f"{args.base}...{args.head}",
                    "--",
                ],
                cwd=ROOT,
                check=True,
                capture_output=True,
            )
            paths = [os.fsdecode(item) for item in diff.stdout.split(b"\0") if item]
            names = select(discover(), paths)
            selection = "CLI playground builds: " + (", ".join(names) or "none")
            print(selection)
            if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
                with Path(summary).open("a") as stream:
                    print(selection, file=stream)
            if names:
                subprocess.run(
                    [
                        "cargo",
                        "build",
                        "--locked",
                        "--release",
                        "--bins",
                        *(arg for name in names for arg in ("--package", name)),
                    ],
                    cwd=ROOT,
                    check=True,
                )
                args.output.mkdir(parents=True, exist_ok=False)
                for name in names:
                    shutil.copy2(ROOT / "target/release" / name, args.output / name)
        else:
            api = GitHub()
            base_url = os.environ.get("PLAYGROUND_URL", "")
            token = os.environ.get("PLAYGROUND_DEPLOY_TOKEN", "")
            if args.command == "lifecycle":
                message = lifecycle(api, args.repo, args.pr, args.action, base_url, token)
            else:
                message = publish(api, args.repo, args.run_id, base_url, token)
            if message:
                print(message)
    except OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError:
        sys.exit("Playground CI failed; check configuration, build logs, and artifact validity.")


if __name__ == "__main__":
    main()
