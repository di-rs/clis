"""Run the real PostgreSQL and fake-sandbox product tier, without native gVisor."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
TESTS = (
    "test_postgres.py",
    "test_api.py",
    "test_sessions.py",
    "test_service.py",
    "test_stop_cleanup.py",
    "test_terminal_limits.py",
    "test_reconnect.py",
    "test_boxie_integration.py",
    "test_boxie_recovery.py",
    "test_correlation_network.py",
    "test_observability.py",
    "test_session_correlation.py",
)

if __name__ == "__main__":
    raise SystemExit(
        subprocess.call(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "integration",
                *(str(Path(__file__).parent / name) for name in TESTS),
                "-q",
                "--durations=10",
            ],
            cwd=ROOT,
        )
    )
