"""Setup uses provider transport with pinned trust and explicit dependencies."""

import runpy
from pathlib import Path

import pulumi
import pytest
from pulumi_command import remote
from test_host import HostMocks

ROOT = Path(__file__).resolve().parents[4]


@pytest.mark.parametrize("revision,run_id", [("main", "1-1"), ("a" * 40, "1;true")])
def test_installation_rejects_untrusted_paths(revision: str, run_id: str) -> None:
    namespace = runpy.run_path(str(ROOT / "infra/environments/single-host/installation.py"))
    with pytest.raises(ValueError):
        namespace["validate_release"](revision, run_id)


@pulumi.runtime.test  # type: ignore[untyped-decorator]
async def test_setup_uploads_before_start_and_preserves_pinned_trust(tmp_path: Path) -> None:
    mocks = HostMocks()
    namespace = runpy.run_path(str(ROOT / "infra/environments/single-host/installation.py"))
    (tmp_path / "revision").write_text("a" * 40)
    connection = remote.ConnectionArgs(
        host="203.0.113.12",
        user="root",
        private_key=pulumi.Output.secret("private-key"),
        host_key="ssh-ed25519 YWJj",
    )
    operation = namespace["provision_installation"](
        connection,
        tmp_path,
        "a" * 40,
        "123-1",
        host_id="host-123",
        settings=pulumi.Output.secret("private-setting"),
        tunnel_credentials=pulumi.Output.secret("private-tunnel"),
        registry_config=pulumi.Output.secret("registry-token"),
        dependencies=[],
    )
    await operation.urn.future()
    await mocks.finish()
    values = mocks.inputs["command:remote:Command", "installation"]
    assert values["connection"].get("value", values["connection"])["hostKey"] == "ssh-ed25519 YWJj"
    assert values["create"] == values["update"]
    assert "chmod -R" not in values["create"]
    assert "chmod 0600" in values["create"]
    assert "delete" not in values
    triggers = (
        values["triggers"].get("value", [])
        if isinstance(values["triggers"], dict)
        else values["triggers"]
    )
    assert "host-123" in triggers
    assert "private-setting" not in values["create"]
    assert "systemctl start clis-setup@123-1.service" in values["create"]
    registration = mocks.registrations["command:remote:Command", "installation"]
    assert registration.opts.depends_on
    assert {"stdout", "stderr"}.issubset(registration.opts.additional_secret_outputs or [])
    copies = [
        value for (kind, _), value in mocks.inputs.items() if kind == "command:remote:CopyToRemote"
    ]
    assert len(copies) == 1
    for name, secret in [
        ("settings.env", "private-setting"),
        ("tunnel.json", "private-tunnel"),
        ("registry-config.json", "registry-token"),
    ]:
        writer = mocks.inputs["command:remote:Command", f"installation-{name}"]
        assert writer["stdin"]["value"] == secret
        assert secret not in writer["create"]
        assert writer["create"] == writer["update"]
        assert "umask 077" in writer["create"]
        assert "host-123" in writer["triggers"]
        assert "123-1" in writer["triggers"]
        assert "a" * 40 in writer["triggers"]
        assert writer["logging"] == "none"
        assert writer["addPreviousOutputInEnv"] is False
    assert all(
        value["connection"].get("value", value["connection"])["hostKey"] == "ssh-ed25519 YWJj"
        for value in copies
    )
