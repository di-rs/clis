"""Hold an OS lock for the full lifetime of a physical execution owner."""

import errno
import fcntl
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def acquire_owner_lock(data_dir: Path) -> Iterator[None]:
    data_dir.mkdir(parents=True, exist_ok=True)
    path = data_dir / "owner.lock"
    try:
        descriptor = path.open("a")
    except OSError as error:
        if error.errno not in {errno.EROFS, errno.EACCES}:
            raise
        # A previous owner already created this inode. Restart cleanup must still
        # acquire its lock when journal storage has become read-only.
        descriptor = path.open("r")
    with descriptor as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("orchestrator data directory already owned") from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
