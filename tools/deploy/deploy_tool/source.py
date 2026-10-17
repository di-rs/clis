"""Read bounded regular blobs from an exact committed revision, never the checkout."""

import os
import subprocess
from pathlib import Path

from .manifests import ReleaseError, relative_file, string


def committed_files(repository: Path, revision: str, paths: tuple[str, ...]) -> dict[str, bytes]:
    string(revision, "committed revision", r"[a-f0-9]{40}|[a-f0-9]{64}")
    # Hook environments can point Git at the caller's index and worktree.
    local = subprocess.run(
        ["git", "rev-parse", "--local-env-vars"], check=True, capture_output=True, text=True
    ).stdout.splitlines()
    env = {key: value for key, value in os.environ.items() if key not in local}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS="1")

    def git(*args: str) -> bytes:
        try:
            return subprocess.run(
                ["git", "--literal-pathspecs", "-C", str(repository), *args],
                env=env,
                check=True,
                capture_output=True,
                timeout=15,
            ).stdout
        except (OSError, subprocess.SubprocessError) as error:
            raise ReleaseError("cannot resolve committed release source") from error

    if git("rev-parse", "--verify", revision + "^{commit}").decode().strip() != revision:
        raise ReleaseError("release revision must identify the exact commit")
    if len(paths) > 65 or len(set(paths)) != len(paths):
        raise ReleaseError("invalid committed source selection")
    result: dict[str, bytes] = {}
    total = 0
    for name in paths:
        relative_file(name)
        entry = git("ls-tree", revision, "--", name).split(b"\t", 1)
        if len(entry) != 2 or entry[1].rstrip(b"\n") != name.encode():
            raise ReleaseError("committed release file is missing")
        fields = entry[0].split()
        if len(fields) != 3 or fields[0] not in (b"100644", b"100755") or fields[1] != b"blob":
            raise ReleaseError("committed release files must be regular blobs")
        oid = fields[2].decode("ascii")
        size = int(git("cat-file", "-s", oid))
        total += size
        if size > 5_000_000 or total > 10_000_000:
            raise ReleaseError("committed release exceeds size limit")
        result[name] = git("cat-file", "blob", oid)
    return result
