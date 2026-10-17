"""Validate GitHub identities through the shared CI transport."""

from ci.github import GitHub, table

WORKFLOW = ".github/workflows/cli-playground.yml"


def positive(value: object, maximum: int = 10**18) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise ValueError("invalid integer")
    return value


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
