"""Explicit synthetic v1 pressure driver; never evidence of lifecycle transitions."""

import datetime
import json
import os
import sys
import time
from pathlib import Path
from uuid import UUID


def main() -> None:
    instance, limit = sys.argv[1], int(sys.argv[2])
    if not 1 <= limit <= 1000000:
        raise ValueError("synthetic pressure event bound exceeded")
    control = Path("/control")
    paced = len(sys.argv) > 3 and sys.argv[3] == "--paced"
    probe = sys.argv[3] if len(sys.argv) > 3 and not paced else None
    for sequence in range(limit):
        if paced and sequence % 1000 == 0:
            # A fresh interval per batch prevents catching up after slow writes.
            # At most 1000 events per 0.1 seconds; the same 1M input takes >=100s.
            until = time.monotonic() + 0.1
            while (remaining := until - time.monotonic()) > 0:
                time.sleep(remaining)
        event = {
            "schema_version": 1,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
            "level": "INFO",
            "event": "smoke.synthetic",
            "message": "synthetic pressure " + "x" * 108,
            "service": "boxie",
            "role": "api",
            "release": "unknown",
            "instance_id": probe if probe and sequence < 32 else instance,
            "logger": "service_telemetry",
            "operation_id": str(UUID(int=sequence + 1)),
            "error_stack": "synthetic padding " + "x" * 490,
        }
        os.write(1, (json.dumps(event) + "\n").encode())
        if sequence % 1000 == 0 or sequence == 31 or sequence == limit - 1:
            temporary = control / "emitted.pending"
            temporary.write_text(
                json.dumps(
                    {
                        "emitted": sequence + 1,
                        "instance": instance,
                        "probe_instance": probe,
                        "probe_emitted": min(sequence + 1, 32) if probe else 0,
                    }
                )
            )
            temporary.replace(control / "emitted.json")


if __name__ == "__main__":
    main()
