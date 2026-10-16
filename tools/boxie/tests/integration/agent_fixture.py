"""Production agent bootstrap with test-only collection entry/completion markers.

The wrapper changes no collection timing, locking, deadline or deletion logic.
"""

import time
from pathlib import Path

import boxie.bootstrap.agent
import boxie.persistence.postgres_artifacts
from boxie.persistence.postgres_artifacts import PostgresArtifactStore


def mark(root: Path, name: str, value: str) -> None:
    temporary = root / (name + ".tmp")
    temporary.write_text(value)
    temporary.replace(root / name)


class ObservedArtifacts(PostgresArtifactStore):
    async def collect(self, limit: int = 100) -> int:
        marker = str(time.time_ns())
        root = Path("/faults")
        mark(root, "collection_entered", marker)
        try:
            return await super().collect(limit)
        finally:
            mark(root, "collection_completed", marker)


if __name__ == "__main__":
    # The production bootstrap imports this class inside _main after logging starts.
    vars(boxie.persistence.postgres_artifacts)["PostgresArtifactStore"] = ObservedArtifacts
    boxie.bootstrap.agent.main()
