"""Linux DAC proof for the lifecycle fixture's real hardened service mounts."""

import json
import os
import stat
from pathlib import Path
from uuid import uuid4

import pytest
from integration.stack import LifecycleStack
from run_integration import ProductionStack, command


def test_temporal_client_fixture_keys_are_private(tmp_path: Path) -> None:
    ProductionStack(tmp_path).certificates()
    keys = list((tmp_path / "clients").glob("*.key"))
    assert {path.name for path in keys} == {
        "boxie-api.key",
        "boxie-supervisor.key",
        "untrusted.key",
    }
    for path in keys:
        assert path.stat().st_uid == os.getuid()
        assert stat.S_ISREG(path.lstat().st_mode)
        assert path.stat().st_mode & 0o077 == 0, path.name


@pytest.mark.integration
@pytest.mark.parametrize("data_mode", ["rw", "ro"])
def test_native_fixture_private_credentials_and_writable_storage(data_mode: str) -> None:
    # A Docker volume keeps inode ownership in Linux, outside Desktop's host-share
    # ownership translation. Only this test's randomly named volume is removed.
    image = os.environ.get("BOXIE_IMAGE", "boxie:lifecycle")
    volume = "boxie-dac-" + uuid4().hex
    command(["docker", "volume", "create", volume])
    try:
        mountpoint = command(["docker", "volume", "inspect", volume, "--format", "{{.Mountpoint}}"])
        command(
            [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--volume",
                volume + ":/owned",
                image,
                "python",
                "-c",
                """
import os, sys
from pathlib import Path
root = Path('/owned')
for name in ('supervisor.secret', 'consumer.secret', 'postgres-dsn', 'supervisor-dsn',
             'clients/boxie-api.key', 'clients/boxie-supervisor.key'):
    path = root / name
    path.parent.mkdir(exist_ok=True)
    path.write_text('private-fixture-credential')
    path.chmod(0o600)
for name in ('api.json', 'supervisor.json', 'profiles.json',
             'clients/frontend-ca.pem', 'clients/boxie-api.pem', 'clients/boxie-supervisor.pem'):
    (root / name).write_text('{}')
for name in ('data', 'data/profiles', 'faults', 'negative'):
    (root / name).mkdir(exist_ok=True)
for path in [root, *root.rglob('*')]:
    os.chown(path, int(sys.argv[1]), int(sys.argv[2]))
for name, mode, owner in (('group', 0o640, int(sys.argv[1])),
                          ('world', 0o604, int(sys.argv[1])),
                          ('foreign', 0o600, int(sys.argv[1]) + 1)):
    path = root / 'negative' / name
    path.write_text('private-fixture-credential')
    path.chmod(mode)
    os.chown(path, owner, int(sys.argv[2]))
(root / 'negative/link').symlink_to('foreign')
""",
                str(os.getuid()),
                str(os.getgid()),
            ]
        )
        stack = LifecycleStack(Path(mountpoint))
        stack.env.update(
            BOXIE_IMAGE=image,
            BOXIE_TEST_PRODUCT_PORT="18000",
            BOXIE_TEST_API_PORT="18090",
            BOXIE_TEST_DATA_MODE=data_mode,
        )
        config = json.loads(stack.runtime_command("config", "--format", "json"))
        data_dir = stack.env["BOXIE_DATA_DIR"]
        for name in ("api", "supervisor"):
            service = config["services"][name]
            args = [
                "docker",
                "run",
                "--rm",
                "--network",
                "none",
                "--read-only",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
            ]
            if service.get("user"):
                args.extend(["--user", service["user"]])
            secrets = []
            for mount in service["volumes"]:
                target = mount["target"]
                if (
                    target.startswith("/run/secrets/")
                    or target == data_dir
                    or target == data_dir + "/profiles"
                ):
                    assert mount["type"] == "bind"
                    if target.startswith("/run/secrets/"):
                        assert mount["read_only"]
                        if not target.endswith(".pem"):
                            secrets.append(target)
                    args.extend(
                        [
                            "--mount",
                            "type=bind,source="
                            + mount["source"]
                            + ",target="
                            + target
                            + (",readonly" if mount.get("read_only") else ""),
                        ]
                    )
            assert service["read_only"] and service["cap_drop"] == ["ALL"]
            assert service["security_opt"] == ["no-new-privileges:true"]
            expected = {
                "/run/secrets/supervisor",
                "/run/secrets/postgres-dsn",
                "/run/secrets/temporal-client.key",
            }
            if name == "api":
                expected.add("/run/secrets/consumer")
            assert set(secrets) == expected
            args.extend(
                [
                    "--mount",
                    "type=bind,source=" + mountpoint + "/negative,target=/negative,readonly",
                ]
            )
            result = command(
                [
                    *args,
                    image,
                    "python",
                    "-c",
                    """
import errno, os, stat, sys
from pathlib import Path
from boxie.bootstrap.config import read_credential
status = Path('/proc/self/status').read_text().splitlines()
assert 'NoNewPrivs:\t1' in status
assert all(int(line.split()[1], 16) == 0 for line in status if line.startswith(('CapEff:', 'CapPrm:', 'CapBnd:')))
for name in sys.argv[4:]:
    path = Path(name)
    assert stat.S_ISREG(path.lstat().st_mode) and path.stat().st_mode & 0o077 == 0
    read_credential(path)
    try:
        path.write_text('must-not-write')
    except OSError as error:
        assert error.errno == errno.EROFS
    else:
        raise AssertionError('secret mount is writable')
for name in ('group', 'world'):
    try:
        read_credential(Path('/negative') / name)
    except ValueError:
        pass
    else:
        raise AssertionError('non-private credential accepted')
for name, expected in (('foreign', errno.EACCES), ('link', errno.ELOOP)):
    try:
        read_credential(Path('/negative') / name)
    except OSError as error:
        assert error.errno == expected
    else:
        raise AssertionError('foreign or symlink credential accepted')
assert os.geteuid() == int(sys.argv[1])
if sys.argv[2] != '-':
    root = Path(sys.argv[2])
    (root / 'profiles/ownership-proof').write_text('owned')
    try:
        (root / 'ownership-proof').write_text('owned')
    except OSError as error:
        assert sys.argv[3] == 'ro' and error.errno == errno.EROFS
    else:
        assert sys.argv[3] == 'rw'
print('uid=' + str(os.geteuid()) + '; zero capabilities; private credentials readable; secret mounts immutable; unsafe credentials rejected; storage mode=' + sys.argv[3])
""",
                    str(os.getuid()),
                    data_dir if name == "supervisor" else "-",
                    data_mode,
                    *secrets,
                ]
            )
            print(name + ": " + result)
    finally:
        command(["docker", "volume", "rm", volume])
