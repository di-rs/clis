"""Standalone SSH-side adapter. Only the Python standard library is required.

The release CLI sends this source as the SSH command and private JSON on stdin.
The internal --root/--health-timeout switches also allow executable local tests.
"""

import argparse
import base64
import fcntl
import hashlib
import json
import os
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from typing import Any


class ActivationError(Exception):
    """Safe operational failure without child output or input values."""


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def bundle_id(bundle: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(bundle["release"])).hexdigest()


def safe_path(root: Path, name: str) -> Path:
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name or "\\" in name or name == ".":
        raise ActivationError("unsafe release path")
    target = root / name
    if target.is_symlink() or any((root / p).is_symlink() for p in path.parents if str(p) != "."):
        raise ActivationError("release path is a symbolic link")
    return target


def write_private(path: Path, content: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as stream:
        stream.write(content)


def read_bundle(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads((path / "bundle.json").read_bytes())
    if bundle_id(value) != path.name:
        raise ActivationError("stored release changed")
    for name, encoded in value["files"].items():
        if safe_path(path, name).read_bytes() != base64.b64decode(encoded, validate=True):
            raise ActivationError("stored release file changed")
    if (path / "environment.json").read_bytes() != canonical(value["environment"]):
        raise ActivationError("stored release environment changed")
    return value


class Activator:
    def __init__(self, root: Path, timeout: float, health_timeout: float) -> None:
        self.root = root
        self.deadline = time.monotonic() + timeout
        self.health_timeout = health_timeout

    def run(self, command: list[str], env: dict[str, str], cwd: Path, seconds: float = 120) -> str:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ActivationError("activation deadline exceeded")
        # Drain a bounded pipe while the child runs; a noisy hook cannot fill /tmp.
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        assert process.stdout is not None
        end = time.monotonic() + min(seconds, remaining)
        output = bytearray()
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    wait = end - time.monotonic()
                    if wait <= 0:
                        raise ActivationError("activation command deadline exceeded")
                    for key, _ in selector.select(min(1, wait)):
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fd)
                            continue
                        if len(output) + len(chunk) > 1_000_000:
                            raise ActivationError("activation command output exceeds limit")
                        output.extend(chunk)
            process.wait(timeout=max(0, end - time.monotonic()))
            if process.returncode != 0:
                raise ActivationError("activation command failed")
            return output.decode()
        except subprocess.TimeoutExpired as error:
            raise ActivationError("activation command deadline exceeded") from error
        finally:
            # Descendants can retain the pipe after the leader exits. Reap the group
            # on every path, including output overflow and cancellation.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
            process.stdout.close()

    def context(
        self, bundle: dict[str, Any], directory: Path, previous: Path | None = None
    ) -> dict[str, str]:
        env = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
        env.update({"LC_ALL": "C", "COMPOSE_IGNORE_ORPHANS": "true"})
        env.update(bundle["environment"])
        env.update(bundle["release"]["images"])
        env.update(
            {
                "CLIS_RELEASE_REVISION": bundle["release"]["revision"],
                "CLIS_RELEASE_ID": directory.name,
                "CLIS_RELEASE_DIR": str(directory),
                "CLIS_COMPOSE_FILE": str(directory / bundle["descriptor"]["compose_file"]),
                "CLIS_COMPOSE_PROJECT": "clis-" + bundle["descriptor"]["component"],
                "CLIS_PREVIOUS_RELEASE_DIR": str(previous) if previous else "",
                "CLIS_DEPLOY_DEADLINE_SECONDS": str(max(0, int(self.deadline - time.monotonic()))),
            }
        )
        return env

    def compose(self, bundle: dict[str, Any], directory: Path) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            "/dev/null",
            "--project-directory",
            str(directory),
            "-p",
            "clis-" + bundle["descriptor"]["component"],
            "-f",
            str(directory / bundle["descriptor"]["compose_file"]),
        ]

    def healthy(self, bundle: dict[str, Any], directory: Path) -> None:
        descriptor = bundle["descriptor"]
        env = self.context(bundle, directory)
        compose = self.compose(bundle, directory)
        end = min(self.deadline, time.monotonic() + self.health_timeout)
        # Resolve content IDs, rather than trusting mutable tags or running status alone.
        intended: dict[str, str] = {}
        for variable, services in descriptor["image_variables"].items():
            if services:
                reference = bundle["release"]["images"][variable]
                intended[variable] = self.run(
                    ["docker", "image", "inspect", "--format", "{{.Id}}", reference],
                    env,
                    directory,
                    15,
                ).strip()
                if not re.fullmatch(r"sha256:[a-f0-9]{64}", intended[variable]):
                    raise ActivationError("invalid intended image identity")
        while True:
            try:
                for service in descriptor["services"]:
                    containers = self.run(
                        compose + ["ps", "-q", service], env, directory, 15
                    ).split()
                    if len(containers) != 1:
                        raise ActivationError("service container absent or ambiguous")
                    data = json.loads(
                        self.run(
                            ["docker", "inspect", "--format", "{{json .}}", containers[0]],
                            env,
                            directory,
                            15,
                        )
                    )
                    variable = next(
                        key
                        for key, members in descriptor["image_variables"].items()
                        if service in members
                    )
                    labels = data["Config"].get("Labels", {})
                    if data["Image"] != intended[variable] or not data["State"]["Running"]:
                        raise ActivationError("service running unintended image")
                    if (
                        labels.get("com.docker.compose.project")
                        != "clis-" + descriptor["component"]
                        or labels.get("com.docker.compose.service") != service
                    ):
                        raise ActivationError("service belongs to another component")
                    check = descriptor.get("healthchecks", {}).get(service, {"kind": "docker"})
                    if check["kind"] == "exec":
                        self.run(
                            compose + ["exec", "-T", service, *check["command"]], env, directory, 15
                        )
                    elif data["State"].get("Health", {}).get("Status") != "healthy":
                        raise ActivationError("service not ready")
                return
            except (ActivationError, KeyError, ValueError):
                if time.monotonic() >= end:
                    raise ActivationError("release readiness failed") from None
                time.sleep(min(1, max(0, end - time.monotonic())))

    def start(self, bundle: dict[str, Any], directory: Path) -> None:
        env = self.context(bundle, directory)
        command = self.compose(bundle, directory)
        services = bundle["descriptor"]["services"]
        self.run(
            command + ["up", "-d", "--no-deps", "--no-build", "--pull", "never", *services],
            env,
            directory,
        )
        self.healthy(bundle, directory)

    def pointer(self, component: Path, name: str) -> Path | None:
        path = component / name
        if not path.is_symlink():
            if path.exists():
                raise ActivationError("release pointer is not a symbolic link")
            return None
        resolved = path.resolve(strict=True)
        if resolved.parent != (component / "releases").resolve():
            raise ActivationError("release pointer escapes component")
        return resolved

    def link(self, component: Path, name: str, destination: Path) -> None:
        staging = component / ("." + name + "-" + str(os.getpid()))
        staging.symlink_to("releases/" + destination.name)
        os.replace(staging, component / name)

    def activate(self, bundle: dict[str, Any]) -> dict[str, str]:
        component_name = bundle["descriptor"]["component"]
        if re.fullmatch(r"[a-z][a-z0-9-]{0,47}", component_name) is None:
            raise ActivationError("invalid component")
        component = self.root / component_name
        if component.is_symlink() or (component / "releases").is_symlink():
            raise ActivationError("component directory cannot be a symbolic link")
        component.mkdir(mode=0o700, parents=True, exist_ok=True)
        releases = component / "releases"
        releases.mkdir(mode=0o700, exist_ok=True)
        lock_path = component / "activation.lock"
        if lock_path.is_symlink():
            raise ActivationError("unsafe activation lock")
        with lock_path.open("a") as lock:
            os.chmod(lock_path, 0o600)
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ActivationError("component activation already in progress") from error
            return self.activate_locked(bundle, component, releases)

    def activate_locked(
        self, bundle: dict[str, Any], component: Path, releases: Path
    ) -> dict[str, str]:
        old = self.pointer(component, "current")
        prior = read_bundle(old) if old else None
        release_id = bundle_id(bundle)
        result = {"component": component.name, "release_id": release_id, "outcome": "incompatible"}
        if prior and (
            prior["release"]["compatibility"] != bundle["release"]["compatibility"]
            or set(prior["descriptor"]["services"]) != set(bundle["descriptor"]["services"])
        ):
            return self.record(component, result)
        directory = releases / release_id
        if directory.exists():
            if read_bundle(directory) != bundle:
                raise ActivationError("immutable release identity collision")
        else:
            staging = Path(tempfile.mkdtemp(prefix=".incoming-", dir=releases))
            write_private(staging / "bundle.json", canonical(bundle))
            write_private(staging / "environment.json", canonical(bundle["environment"]))
            for name, value in bundle["files"].items():
                write_private(safe_path(staging, name), base64.b64decode(value, validate=True))
            os.rename(staging, directory)
        env = self.context(bundle, directory, old)
        command = self.compose(bundle, directory)
        # Validate and pull before a drain hook may stop the previous workload.
        try:
            self.run(command + ["config", "--quiet"], env, directory)
            self.run(
                command + ["pull", "--policy", "always", *bundle["descriptor"]["services"]],
                env,
                directory,
            )
        except ActivationError:
            result["outcome"] = "preflight-failed"
            return self.record(component, result)
        hook_failed = False
        self.record(component, {**result, "outcome": "activating"})
        overall_deadline = self.deadline
        if prior:
            remaining = max(0, overall_deadline - time.monotonic())
            self.deadline -= min(270, remaining * 0.4)
        try:
            hook = bundle["descriptor"].get("pre_activate")
            if hook:
                try:
                    self.run(["sh", str(safe_path(directory, hook))], env, directory, 90)
                except ActivationError:
                    hook_failed = True
                    raise
            self.start(bundle, directory)
        except ActivationError:
            self.deadline = overall_deadline
            if prior and old:
                try:
                    self.start(prior, old)
                    result["outcome"] = "failed-rolled-back"
                except ActivationError:
                    result["outcome"] = "failed-rollback-failed"
            else:
                result["outcome"] = "hook-failed" if hook_failed else "failed-no-previous"
            return self.record(component, result)
        self.deadline = overall_deadline
        if old and old != directory:
            self.link(component, "previous", old)
        self.link(component, "current", directory)
        result["outcome"] = "activated"
        return self.record(component, result)

    def record(self, component: Path, result: dict[str, str]) -> dict[str, str]:
        temporary = component / (".status-" + str(os.getpid()))
        write_private(temporary, canonical(result))
        os.replace(temporary, component / "status.json")
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("/opt/clis"))
    parser.add_argument("--timeout", type=float, default=870)
    parser.add_argument("--health-timeout", type=float, default=120)
    args = parser.parse_args()
    try:
        if not 0 < args.timeout <= 870 or not 0 < args.health_timeout <= 180:
            raise ActivationError("invalid activation deadline")
        bundle = json.loads(sys.stdin.buffer.read(20_000_001))
        result = Activator(args.root.resolve(), args.timeout, args.health_timeout).activate(bundle)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["outcome"] == "activated" else 1
    except (ActivationError, OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"outcome": "activation-error"}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
