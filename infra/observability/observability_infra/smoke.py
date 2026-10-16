"""Native isolated Boxie observability smoke entrypoint.

Each scenario has its own 180-second monotonic budget, including restoration.
Setup and final resource teardown are separate bounded operations. No cloud API,
shared daemon restart, host-journal change, image publication or silent fallback.
"""

import argparse
import asyncio
import dataclasses
import json
import os
import resource
import shutil
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from observability_infra.smoke_application import (
    REPO,
    Application,
    application_overlay,
    save_stack,
    stack_at,
)
from observability_infra.smoke_diagnostics import setup_diagnostics
from observability_infra.smoke_resources import Resources, require_ephemeral_runner
from observability_infra.smoke_runtime import Commands, Deadline, evidence
from observability_infra.smoke_scenarios import SCENARIOS, Scenario
from observability_infra.smoke_telemetry import Telemetry
from observability_infra.smoke_watchdog import restore_registered


@dataclass(frozen=True)
class SmokeConfig:
    root: Path
    scenario: str

    def __post_init__(self) -> None:
        if self.scenario not in SCENARIOS:
            raise ValueError("unknown required smoke scenario")


@dataclass(frozen=True)
class SmokeReport:
    scenario: str
    status: str
    elapsed_seconds: float
    observations: dict[str, Any]
    cleanup_errors: tuple[str, ...]


def run_smoke(config: SmokeConfig, timeout_seconds: float = 180) -> SmokeReport:
    require_ephemeral_runner()
    resource.setrlimit(resource.RLIMIT_AS, (768 * 1024**2, 768 * 1024**2))
    deadline = Deadline(timeout_seconds, reserve=min(45, timeout_seconds / 3))
    commands = Commands(deadline)
    application = Application(config.root, commands)
    telemetry = Telemetry(config.root, commands, application.stack)
    scenario = Scenario(config.scenario, application, telemetry)
    cleanup_errors: list[str] = []
    errors: list[str] = []

    async def exercise() -> None:
        try:
            async with asyncio.timeout(deadline.remaining()):
                await scenario.run()
        except Exception as error:
            errors.append(type(error).__name__)
            scenario.observations["failure_location"] = [
                {"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
                for frame in traceback.extract_tb(error.__traceback__)[-8:]
            ]
        finally:
            commands.restoring = True
            # Independent restore always precedes application/Docker cleanup.
            for operation in (scenario.restore, application.close):
                try:
                    async with asyncio.timeout(deadline.remaining(restoring=True)):
                        await operation()
                except Exception as error:
                    cleanup_errors.append(type(error).__name__)
        if not cleanup_errors:
            try:
                # Same production client and query endpoints must be usable again.
                async with asyncio.timeout(deadline.remaining(restoring=True)):
                    for service in ("vector", "victorialogs"):
                        while True:
                            try:
                                await telemetry.metrics(service)
                                break
                            except Exception:
                                deadline.remaining(restoring=True)
                                await asyncio.sleep(0.1)
            except Exception as error:
                cleanup_errors.append(type(error).__name__)

    asyncio.run(exercise())
    scenario.observations["errors"] = errors
    result = SmokeReport(
        config.scenario,
        "failed" if errors or cleanup_errors else "passed",
        deadline.elapsed,
        scenario.observations,
        tuple(cleanup_errors),
    )
    evidence(config.root / ("scenario-" + config.scenario + ".json"), dataclasses.asdict(result))
    return result


def prepare(root: Path, timeout_seconds: float = 1200) -> None:
    require_ephemeral_runner()
    deadline = Deadline(timeout_seconds, reserve=60)
    commands = Commands(deadline)
    resources = Resources(root, commands)
    stage = "owned daemon and journal"
    stack: Any = None
    private_sentinels: tuple[str, ...] = ()
    try:
        resources.prepare()
        os.environ["DOCKER_HOST"] = "unix://" + str(resources.socket)
        env = dict(os.environ)
        for tag, dockerfile, context in (
            ("clis-playground-sandbox:lifecycle", "tools/cli-playground/Sandbox.Dockerfile", "."),
            ("boxie:lifecycle", "tools/boxie/infra/Runtime.Dockerfile", "."),
            (
                "boxie-collector:smoke",
                "infra/observability/collector/Dockerfile",
                "infra/observability/collector",
            ),
        ):
            stage = "build " + tag
            commands.run(
                [
                    "docker",
                    "build",
                    "--quiet",
                    "--network=host",
                    "--platform=linux/amd64",
                    "--file",
                    str(REPO / dockerfile),
                    "--tag",
                    tag,
                    str(REPO / context),
                ],
                env=env,
            )
        stage = "native binary"
        commands.run(
            ["cargo", "build", "--quiet", "--locked", "--release", "--package", "echor"], env=env
        )
        (root / "application").mkdir(mode=0o755)
        (root / "control").mkdir(mode=0o777)
        (root / "control").chmod(0o777)
        application_overlay(root)
        stack = stack_at(root, commands)
        stack.build_network = "host"  # Build only; runtime private bridges have no outbound NAT.
        stage = "real lifecycle dependencies"
        save_stack(root, stack)
        try:
            stack.start()
        finally:
            save_stack(root, stack)
        stage = "final collector and stores"
        telemetry = Telemetry(root, commands, stack)
        telemetry.prepare(resources)
        stage = "real HTTP PTY and binary journal preflight"
        application = Application(root, commands)
        private_sentinels = application.sentinels

        async def preflight() -> None:
            async with asyncio.timeout(deadline.remaining()):
                sandbox = await application.provision()
                progress = await application.progress(sandbox)
                records = await telemetry.wait_logs(f'sandbox_id:"{sandbox}"')
                await application.close()
                evidence(
                    root / "preflight.json",
                    {
                        "progress": progress,
                        "real_sandbox_records": len(records),
                        "sandbox": sandbox,
                        "resources": await asyncio.to_thread(telemetry.resources),
                    },
                )

        asyncio.run(preflight())
        evidence(
            root / "setup.json",
            {
                "status": "passed",
                "elapsed_seconds": deadline.elapsed,
                "arrangement": "owned journald namespace and dockerd",
                "fallback": False,
            },
        )
    except Exception as error:
        if root.exists():
            if stack is not None:
                setup_diagnostics(
                    root,
                    stack,
                    commands.last_failure,
                    private_sentinels,
                    failed_build_output=commands.failed_build_output,
                )
            evidence(
                root / "setup.json",
                {
                    "status": "failed",
                    "stage": stage,
                    "error_type": type(error).__name__,
                    "elapsed_seconds": deadline.elapsed,
                    "failure_location": [
                        {
                            "file": Path(frame.filename).name,
                            "function": frame.name,
                            "line": frame.lineno,
                        }
                        for frame in traceback.extract_tb(error.__traceback__)[-8:]
                    ],
                },
            )
        raise


def cleanup(root: Path) -> None:
    require_ephemeral_runner()
    if not (root / "ownership.json").exists():
        return
    commands = Commands(Deadline(180, reserve=1))
    commands.restoring = True
    resources = Resources(root, commands)
    errors: list[str] = []
    restorations: dict[str, Any] = {}
    for path in root.glob("watch-*.json"):
        if path.name.endswith(".result.json"):
            continue
        try:
            restorations[path.name] = restore_registered(path, commands)
        except Exception as error:
            restorations[path.name] = {"restored": False, "error_type": type(error).__name__}
    if any(item["restored"] is not True for item in restorations.values()):
        evidence(
            root / "cleanup.json",
            {
                "status": "failed",
                "restorations": restorations,
                "errors": ["fault restoration incomplete; teardown withheld"],
                "mounts_remaining": [
                    path for path in resources.state["mounts"] if os.path.ismount(path)
                ],
            },
        )
        raise RuntimeError("fault restoration incomplete; teardown withheld")
    stack = stack_at(root, commands) if (root / "application-private.json").exists() else None
    if stack:
        telemetry = Telemetry(root, commands, stack)
        for operation in (
            lambda: (
                telemetry.compose("down", "--volumes", "--remove-orphans")
                if telemetry.rendered.exists()
                else None
            ),
            stack.close,
        ):
            try:
                operation()
            except Exception as error:
                errors.append(type(error).__name__)
    try:
        result = resources.close()
    except Exception as error:
        errors.append(type(error).__name__)
        path = root / "cleanup.json"
        result = json.loads(path.read_text()) if path.exists() else {}
        result["status"] = "failed"
    result["restorations"] = restorations
    result["application_cleanup_errors"] = errors
    evidence(root / "cleanup.json", result)
    if errors:
        raise RuntimeError("owned fixture cleanup incomplete")
    for name in ("application", "application-private.json", "unused-password"):
        path = root / name
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink(missing_ok=True)


def export_evidence(root: Path, destination: Path) -> None:
    # Exact bounded nonsecret allowlist; never upload runtime directories.
    names = [
        "ownership.json",
        "setup.json",
        "setup-diagnostics.json",
        "preflight.json",
        "collector-provenance.json",
        "cleanup.json",
        *("scenario-" + name + ".json" for name in SCENARIOS),
        *("watch-" + name + ".result.json" for name in SCENARIOS),
    ]
    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = root / name
        if source.is_file() and source.stat().st_size <= 262144:
            data = json.loads(source.read_text())
            evidence(destination / name, data)
            (destination / name).chmod(0o644)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "scenario", "cleanup", "export"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scenario", choices=SCENARIOS)
    parser.add_argument("--evidence-dir", type=Path)
    args = parser.parse_args()
    try:
        if args.action == "prepare":
            prepare(args.root)
        elif args.action == "cleanup":
            cleanup(args.root)
        elif args.action == "export":
            if not args.evidence_dir:
                parser.error("export requires --evidence-dir")
            export_evidence(args.root, args.evidence_dir)
        else:
            if not args.scenario:
                parser.error("scenario requires --scenario")
            result = run_smoke(SmokeConfig(args.root, args.scenario))
            print(
                json.dumps(
                    {
                        "scenario": result.scenario,
                        "status": result.status,
                        "elapsed_seconds": result.elapsed_seconds,
                    }
                )
            )
            if result.status != "passed":
                raise SystemExit(1)
    except Exception as error:
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
