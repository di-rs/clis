"""Small typed handles passed from environments to independent infrastructure owners."""

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from uuid import UUID

_NAME = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}\Z")
IMAGE = r"[a-z0-9][a-z0-9./:_-]+@sha256:[a-f0-9]{64}"
REFERENCE = r"[A-Z][A-Z0-9_]{0,127}"


def name(value: str) -> str:
    if not _NAME.fullmatch(value):
        raise ValueError("invalid explicit resource handle")
    return value


@dataclass(frozen=True)
class HostHandle:
    installation_id: str
    host_id: str
    data_root: str

    def __post_init__(self) -> None:
        if str(UUID(self.installation_id)) != self.installation_id:
            raise ValueError("canonical installation UUID required")
        name(self.host_id)
        path = PurePosixPath(self.data_root)
        if (
            not path.is_absolute()
            or str(path) != self.data_root
            or ".." in path.parts
            or path == PurePosixPath("/")
        ):
            raise ValueError("explicit persistent data root required")


@dataclass(frozen=True)
class NetworkHandles:
    control: str
    consumer: str
    metrics: str
    grafana_origin: str
    temporal_ui_origin: str
    public_proxy: str

    def __post_init__(self) -> None:
        values = (
            self.control,
            self.consumer,
            self.metrics,
            self.grafana_origin,
            self.temporal_ui_origin,
            self.public_proxy,
        )
        for value in values:
            name(value)
        if len(set(values)) != len(values):
            raise ValueError("private boundary networks must be independent")

    @classmethod
    def for_installation(cls, installation_id: str) -> NetworkHandles:
        prefix = "clis-" + UUID(installation_id).hex
        return cls(
            *(
                f"{prefix}-{suffix}"
                for suffix in (
                    "control",
                    "consumer",
                    "metrics",
                    "grafana-origin",
                    "temporal-ui-origin",
                    "public-proxy",
                )
            )
        )
