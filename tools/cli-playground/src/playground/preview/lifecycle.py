"""Reconcile observed PR close/reopen events before allowing new publication."""

import re
import urllib.parse

from ci.github import GitHub, PaginationLimit, table

from .github import WORKFLOW, current_pull, positive
from .publication import GatewayRequest, gateway_request, public_origin, publish


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
        # A processed close must invalidate transfers even if GitHub already
        # reports the same SHA reopened. Reconcile availability after invalidation;
        # the reopen event may already have finished before this close is replayed.
        if action != "closed" or not current_pull(api, repo, number, state="open"):
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
        message += (
            " GitHub deployment status listing reached its pagination limit; "
            "some GitHub statuses may remain active."
        )
        deployments = []
    for deployment in deployments:
        if deployment.get("environment") != f"cli-playground/pr-{number}":
            continue
        # Reopening can race with cleanup; reconcile the current state below.
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
    if current_pull(api, repo, number, state="open"):
        return lifecycle(api, repo, number, "reopened", base_url, token, gateway=gateway)
    return message
