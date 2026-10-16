"""Explicit synthetic v1 pressure driver; never evidence of lifecycle transitions."""

import datetime
import json
import os
import sys
from pathlib import Path
from uuid import UUID


def main() -> None:
    instance, limit = sys.argv[1], int(sys.argv[2])
    control = Path("/control")
    for sequence in range(limit):
        event = {
            "schema_version": 1,
            "timestamp": datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z"),
            "level": "INFO",
            "event": "smoke.synthetic",
            "message": "synthetic pressure " + "x" * 108,
            "service": "boxie",
            "role": "api",
            "release": "unknown",
            "instance_id": instance,
            "logger": "service_telemetry",
            "operation_id": str(UUID(int=sequence + 1)),
            "error_stack": "synthetic padding " + "x" * 490,
        }
        os.write(1, (json.dumps(event) + "\n").encode())
        if sequence % 1000 == 0:
            (control / "emitted.json").write_text(
                json.dumps({"emitted": sequence + 1, "instance": instance})
            )
    (control / "emitted.json").write_text(json.dumps({"emitted": limit, "instance": instance}))


if __name__ == "__main__":
    main()
