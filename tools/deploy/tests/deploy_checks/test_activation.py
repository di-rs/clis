"""Exercise the shipped remote executable against a stateful Docker boundary."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from .test_cli import DIGEST, PACKAGE, invoke


def test_noisy_child_is_killed_at_output_limit_before_its_deadline(tmp_path: Path) -> None:
    from deploy_tool.remote import ActivationError, Activator

    started = time.monotonic()
    with pytest.raises(ActivationError, match="output exceeds limit"):
        Activator(tmp_path, timeout=10, health_timeout=1).run(
            [sys.executable, "-c", "import os,time; os.write(1,b'x'*1100000); time.sleep(8)"],
            dict(os.environ),
            tmp_path,
            seconds=5,
        )
    assert time.monotonic() - started < 3


FAKE_DOCKER = r"""#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
state_path = pathlib.Path(os.environ['TEST_DOCKER_STATE'])
state = json.loads(state_path.read_text())
state.setdefault('calls', []).append(args)
if args[0] == 'compose':
    project = args[args.index('-p') + 1]
    path = args[args.index('-f') + 1]
    action = next(a for a in args if a in ('config', 'pull', 'up', 'ps', 'exec', 'stop'))
    if action == 'up':
        state['active'] = os.environ['APP_IMAGE']
        state['project'] = project
        state['release_id'] = os.environ['CLIS_RELEASE_ID']
    if action == 'ps':
        print('container-' + args[-1])
    if action == 'exec':
        if state.get('active') == state.get('fail_image'):
            state_path.write_text(json.dumps(state))
            sys.exit(1)
elif args[:2] == ['image', 'inspect']:
    print('sha256:' + args[-1].split('sha256:')[-1])
elif args[0] == 'inspect':
    active = state['active']
    image_id = 'sha256:' + active.split('sha256:')[-1]
    if state.get('wrong_image'):
        image_id = 'sha256:' + 'f' * 64
    print(json.dumps({'Config': {'Image': active, 'Labels': {
      'clis.release-id': 'old' if state.get('old_release_label') else state['release_id'],
      'com.docker.compose.project': state['project'],
      'com.docker.compose.service': args[-1].replace('container-', ''),
    }}, 'Image': image_id, 'State': {'Running': True, 'Health': {
      'Status': 'unhealthy' if active == state.get('fail_image') else 'healthy'
    }}}))
else:
    state_path.write_text(json.dumps(state))
    sys.exit(72)
state_path.write_text(json.dumps(state))
"""


def prepare_release(source: Path, name: str, digit: str = "a", schema: str = "sample-v1") -> Path:
    release = json.loads((source / "release.json").read_text())
    release["revision"] = digit * 40
    release["images"]["APP_IMAGE"] = "registry.example/test@sha256:" + digit * 64
    release["compatibility"]["schema"] = schema
    descriptor = json.loads((source / "descriptor.json").read_text())
    descriptor["compatibility"]["schema"] = schema
    (source / "descriptor.json").write_text(json.dumps(descriptor))
    (source / "release.json").write_text(json.dumps(release))
    out = source / name
    result = invoke(
        "prepare",
        "--descriptor",
        str(source / "descriptor.json"),
        "--release",
        str(source / "release.json"),
        "--environment",
        str(source / "env.json"),
        "--output",
        str(out),
    )
    assert result.returncode == 0, result.stderr
    return out


def remote(
    prepared: Path, root: Path, bin_dir: Path, *, timeout: float = 20
) -> subprocess.CompletedProcess[str]:
    # The payload is the same private input consumed by the actual SSH-side program.
    from deploy_tool.prepare import load_prepared

    return subprocess.run(
        [
            sys.executable,
            str(PACKAGE / "deploy_tool/remote.py"),
            "--root",
            str(root),
            "--timeout",
            str(timeout),
            "--health-timeout",
            "0.08",
        ],
        input=json.dumps(load_prepared(prepared)),
        text=True,
        capture_output=True,
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
        timeout=10,
        check=False,
    )


@pytest.fixture
def docker(source: Path) -> tuple[Path, Path]:
    bin_dir = source / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "docker").chmod(0o700)
    state = source / "docker.json"
    state.write_text("{}")
    release = json.loads((source / "release.json").read_text())
    release["settings"] = {"TOKEN_REF": "test/v1", "TEST_DOCKER_STATE": str(state)}
    (source / "release.json").write_text(json.dumps(release))
    return bin_dir, state


def test_failed_first_activation_keeps_state_without_success_pointer(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    state.write_text(json.dumps({"fail_image": DIGEST}))
    prepared = prepare_release(source, "first")
    root = source / "host"
    persistent = root / "persistent-data"
    persistent.mkdir(parents=True)
    (persistent / "data").write_text("must survive")
    result = remote(prepared, root, bin_dir)
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["outcome"] == "failed-no-previous"
    assert not (root / "sample/current").exists()
    assert (persistent / "data").read_text() == "must survive"
    assert "never-log-me" not in result.stdout + result.stderr
    assert len(list((root / "sample/releases").iterdir())) == 1
    calls = json.loads(state.read_text())["calls"]
    assert all("down" not in call and "rm" not in call and "prune" not in call for call in calls)


def test_compatible_failure_restores_previous_healthy_image(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    first = prepare_release(source, "first")
    root = source / "host"
    result = remote(first, root, bin_dir)
    assert result.returncode == 0, result.stdout + result.stderr
    old = (root / "sample/current").resolve()
    new_image = "registry.example/test@sha256:" + "b" * 64
    state.write_text(json.dumps({**json.loads(state.read_text()), "fail_image": new_image}))
    second = prepare_release(source, "second", "b")
    result = remote(second, root, bin_dir)
    assert result.returncode == 1, result.stdout + result.stderr
    assert json.loads(result.stdout)["outcome"] == "failed-rolled-back"
    assert (root / "sample/current").resolve() == old
    assert json.loads(state.read_text())["active"] == DIGEST
    assert len(list((root / "sample/releases").iterdir())) == 2
    for call in json.loads(state.read_text())["calls"]:
        if call[0] == "compose":
            assert call[call.index("-p") + 1] == "clis-sample"
            assert call[call.index("-f") + 1].startswith(str(root / "sample/releases"))
        if "up" in call:
            assert "--no-deps" in call and "--no-build" in call
            assert call[-1] == "web"


def test_schema_change_is_blocked_before_hook_or_container_mutation(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    first = prepare_release(source, "first")
    root = source / "host"
    assert remote(first, root, bin_dir).returncode == 0
    before = state.read_bytes()
    second = prepare_release(source, "second", "b", schema="sample-v2")
    result = remote(second, root, bin_dir)
    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome"] == "incompatible"
    assert state.read_bytes() == before


def test_running_wrong_image_is_not_healthy(source: Path, docker: tuple[Path, Path]) -> None:
    bin_dir, state = docker
    state.write_text('{"wrong_image":true}')
    result = remote(prepare_release(source, "first"), source / "host", bin_dir)
    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome"] == "failed-no-previous"


def test_service_owned_hook_receives_context_and_failure_prevents_up(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    descriptor = json.loads((source / "descriptor.json").read_text())
    descriptor["pre_activate"] = "hooks/drain.sh"
    (source / "descriptor.json").write_text(json.dumps(descriptor))
    (source / "hooks").mkdir()
    (source / "hooks/drain.sh").write_text(
        'test "$CLIS_COMPOSE_PROJECT" = clis-sample || exit 22\n'
        'test -f "$CLIS_COMPOSE_FILE" || exit 23\n'
        'test -z "$CLIS_PREVIOUS_RELEASE_DIR" || exit 24\n'
        'echo "$TOKEN"\nexit 25\n'
    )
    result = remote(prepare_release(source, "first"), source / "host", bin_dir)
    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome"] == "hook-failed"
    assert "never-log-me" not in result.stdout + result.stderr
    assert not any("up" in call for call in json.loads(state.read_text())["calls"])


def test_success_updates_previous_and_injects_immutable_release_context(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    first = prepare_release(source, "first")
    root = source / "host"
    assert remote(first, root, bin_dir).returncode == 0
    old = (root / "sample/current").resolve()
    second = prepare_release(source, "second", "b")
    result = remote(second, root, bin_dir)
    assert result.returncode == 0
    assert (root / "sample/previous").resolve() == old
    current = (root / "sample/current").resolve()
    assert current != old
    assert (current / "environment.json").stat().st_mode & 0o777 == 0o600
    assert json.loads((root / "sample/status.json").read_text())["outcome"] == "activated"
    assert json.loads(state.read_text())["active"] == "registry.example/test@sha256:" + "b" * 64


def test_failed_rollback_is_reported_and_does_not_promote_candidate(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    first = prepare_release(source, "first")
    root = source / "host"
    assert remote(first, root, bin_dir).returncode == 0
    old = (root / "sample/current").resolve()
    state.write_text(json.dumps({**json.loads(state.read_text()), "wrong_image": True}))
    result = remote(prepare_release(source, "second", "b"), root, bin_dir)
    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome"] == "failed-rollback-failed"
    assert (root / "sample/current").resolve() == old
    assert (
        json.loads((root / "sample/status.json").read_text())["outcome"] == "failed-rollback-failed"
    )


def test_component_lock_blocks_second_activation_without_docker_mutation(
    source: Path, docker: tuple[Path, Path]
) -> None:
    import fcntl

    bin_dir, state = docker
    root = source / "host"
    component = root / "sample"
    component.mkdir(parents=True)
    with (component / "activation.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = remote(prepare_release(source, "first"), root, bin_dir)
    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome"] == "activation-error"
    assert state.read_text() == "{}"


def test_hook_deadline_is_enforced_and_secret_output_is_suppressed(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    descriptor = json.loads((source / "descriptor.json").read_text())
    descriptor["pre_activate"] = "slow.sh"
    (source / "descriptor.json").write_text(json.dumps(descriptor))
    (source / "slow.sh").write_text('echo "$TOKEN"\nsleep 60\n')
    result = remote(prepare_release(source, "first"), source / "host", bin_dir, timeout=0.8)
    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome"] == "hook-failed"
    assert "never-log-me" not in result.stdout + result.stderr
    assert not any("up" in call for call in json.loads(state.read_text())["calls"])


def test_exec_healthcheck_uses_component_service_and_checks_running_image(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    descriptor = json.loads((source / "descriptor.json").read_text())
    descriptor["healthchecks"] = {
        "web": {"kind": "exec", "command": ["python", "-m", "sample.health"]}
    }
    (source / "descriptor.json").write_text(json.dumps(descriptor))
    assert remote(prepare_release(source, "first"), source / "host", bin_dir).returncode == 0
    calls = json.loads(state.read_text())["calls"]
    assert any(
        call[-6:] == ["exec", "-T", "web", "python", "-m", "sample.health"] for call in calls
    )


def test_remote_executable_parses_on_provisioned_python312() -> None:
    # Ubuntu 24.04 supplies Python3.12; local Python must not mask a newer syntax requirement.
    import ast

    ast.parse((PACKAGE / "deploy_tool/remote.py").read_text(), feature_version=(3, 12))


def test_deploy_cli_runs_shipped_remote_over_private_stdin(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    root = source / "host"
    ssh = bin_dir / "ssh"
    ssh.write_text("""#!/usr/bin/env python3
import os, shlex, subprocess, sys
command = shlex.split(sys.argv[-1])
command += ['--root', os.environ['TEST_REMOTE_ROOT'], '--health-timeout', '0.2']
result = subprocess.run(command, input=sys.stdin.buffer.read(), capture_output=True)
sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr)
sys.exit(result.returncode)
""")
    ssh.chmod(0o700)
    profile = source / "profile.json"
    profile.write_text(
        json.dumps(
            {
                "host": "192.0.2.7",
                "identity_ref": "DEPLOY_SSH_KEY",
                "host_public_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEY5jUnHMEJzWwU5V1fAG0vQZ6nNoMFhD3w2M2rLm1dX",
            }
        )
    )
    key = source / "identity"
    key.write_text("private-key")
    key.chmod(0o600)
    result = invoke(
        "deploy",
        "--prepared",
        str(prepare_release(source, "first")),
        "--profile",
        str(profile),
        "--identity",
        str(key),
        env={"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"], "TEST_REMOTE_ROOT": str(root)},
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["outcome"] == "activated"
    assert (root / "sample/current").is_symlink()
    assert json.loads(state.read_text())["active"] == DIGEST
    assert "never-log-me" not in result.stdout + result.stderr


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGHUP])
def test_cancellation_reaps_hook_before_releasing_component_lock(
    source: Path, docker: tuple[Path, Path], signum: int
) -> None:
    from deploy_tool.prepare import load_prepared

    bin_dir, state = docker
    descriptor = json.loads((source / "descriptor.json").read_text())
    descriptor["pre_activate"] = "cancel.sh"
    (source / "descriptor.json").write_text(json.dumps(descriptor))
    marker = source / "child-started"
    mutated = source / "late-mutation"
    (source / "cancel.sh").write_text(
        f"(sleep 1; echo survived > '{mutated}') &\necho ready > '{marker}'\nwait\n"
    )
    prepared = prepare_release(source, "cancelled")
    root = source / "host"
    process = subprocess.Popen(
        [sys.executable, str(PACKAGE / "deploy_tool/remote.py"), "--root", str(root)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"]},
    )
    try:
        assert process.stdin is not None
        process.stdin.write(json.dumps(load_prepared(prepared)))
        process.stdin.close()
        end = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < end:
            time.sleep(0.01)
        assert marker.exists()
        process.send_signal(signum)
        process.wait(timeout=3)
        assert process.returncode != 0
        status = json.loads((root / "sample/status.json").read_text())
        assert status["outcome"] == "recovery-required"
        import fcntl

        with (root / "sample/activation.lock").open() as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        time.sleep(1.1)
        assert not mutated.exists(), "owned descendant survived lock release"
        assert not (root / "sample/current").exists()
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def test_same_image_with_old_release_label_is_not_promoted(
    source: Path, docker: tuple[Path, Path]
) -> None:
    bin_dir, state = docker
    state.write_text('{"old_release_label":true}')
    result = remote(prepare_release(source, "old-label"), source / "host", bin_dir)
    assert result.returncode == 1
    assert json.loads(result.stdout)["outcome"] == "failed-no-previous"
