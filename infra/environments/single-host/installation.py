"""Initial installation through Pulumi's pinned SSH transport."""

import re
from pathlib import Path

import pulumi
from pulumi_command import remote


def validate_release(revision: str, run_id: str) -> None:
    if not re.fullmatch(r"[a-f0-9]{40}", revision) or not re.fullmatch(r"[0-9]+-[0-9]+", run_id):
        raise ValueError("Use a full commit SHA and numeric run ID-attempt")


def provision_installation(
    connection: remote.ConnectionArgs,
    source: Path,
    revision: str,
    run_id: str,
    *,
    host_id: pulumi.Input[str],
    settings: pulumi.Input[str],
    tunnel_credentials: pulumi.Input[str],
    dependencies: list[pulumi.Resource],
) -> remote.Command:
    validate_release(revision, run_id)
    parent = f"/opt/clis/staging/setup/{run_id}"
    staging = parent + "/source"
    release = f"/opt/clis/releases/setup/{run_id}"
    prepare_command = f"cloud-init status --wait >/dev/null && install -d -m 0700 {parent} /opt/clis/releases/setup && test ! -e {staging}"
    prepare = remote.Command(
        "installation-prepare",
        connection=connection,
        create=prepare_command,
        update=prepare_command,
        triggers=[revision, run_id, host_id],
        opts=pulumi.ResourceOptions(depends_on=dependencies),
    )
    archive = pulumi.FileArchive(str(source))
    copied = remote.CopyToRemote(
        "installation-source",
        connection=connection,
        source=archive,
        remote_path=staging,
        triggers=[revision, run_id, host_id],
        opts=pulumi.ResourceOptions(depends_on=[prepare]),
    )
    private_files = [
        remote.CopyToRemote(
            f"installation-{name}",
            connection=connection,
            source=pulumi.Output.secret(content).apply(pulumi.StringAsset),
            remote_path=f"{staging}/{name}",
            opts=pulumi.ResourceOptions(depends_on=[copied]),
        )
        for name, content in [("settings.env", settings), ("tunnel.json", tunnel_credentials)]
    ]
    command = (
        f"test ! -e {release} && chmod -R go-rwx {staging} && mv {staging} {release} && "
        f"systemctl start clis-setup@{run_id}.service && "
        f'test "$(systemctl show clis-setup@{run_id}.service -p Result --value)" = success && '
        f'test "$(cat /srv/clis/deployment/setup/current/revision)" = {revision}'
    )
    return remote.Command(
        "installation",
        connection=connection,
        create=command,
        update=command,
        triggers=[archive, revision, run_id, host_id, settings, tunnel_credentials],
        add_previous_output_in_env=False,
        logging=remote.Logging.NONE,
        opts=pulumi.ResourceOptions(
            depends_on=private_files, additional_secret_outputs=["stdout", "stderr"]
        ),
    )


def serialize_settings(values: dict[str, str]) -> str:
    for key, value in values.items():
        if not re.fullmatch(r"[A-Z_]+", key) or any(char in value for char in "\n\r\x00'$`\\"):
            raise ValueError(f"Invalid installation setting: {key}")
    return "".join(f"{key}='{value}'\n" for key, value in values.items())
