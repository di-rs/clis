"""Hold an OS lock for the full lifetime of a physical execution owner."""

import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def acquire_owner_lock(data_dir: Path) -> Iterator[None]:
    data_dir.mkdir(parents=True, exist_ok=True)
    with (data_dir / "owner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("orchestrator data directory already owned") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
