"""Strict terminal wire controls; PTY payloads remain opaque binary frames."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from boxie.sandbox.protocol import WireModel

Cursor = Annotated[int, Field(ge=0, le=2**53 - 1)]
Epoch = Annotated[int, Field(ge=1, le=2**53 - 1)]


class AttachRequest(WireModel):
    type: Literal["attach"] = "attach"
    attachment_order: Epoch
    resume_after: Cursor
    cols: Annotated[int, Field(ge=1, le=500)]
    rows: Annotated[int, Field(ge=1, le=300)]


class Input(WireModel):
    type: Literal["input"]
    epoch: Epoch
    source: Literal["user", "terminal"]
    data: Annotated[str, Field(min_length=1, max_length=8192)]


class Resize(WireModel):
    type: Literal["resize"]
    epoch: Epoch
    cols: Annotated[int, Field(ge=1, le=500)]
    rows: Annotated[int, Field(ge=1, le=300)]


class Ack(WireModel):
    type: Literal["ack"]
    epoch: Epoch
    sequence: Cursor


class Heartbeat(WireModel):
    type: Literal["heartbeat"]
    epoch: Epoch
    nonce: Cursor


class Detach(WireModel):
    type: Literal["detach"]
    epoch: Epoch


control = TypeAdapter(
    Annotated[
        AttachRequest | Input | Resize | Ack | Heartbeat | Detach, Field(discriminator="type")
    ]
)


class Attached(WireModel):
    type: Literal["attached"]
    epoch: Epoch


class ReplayComplete(WireModel):
    type: Literal["replay_complete"]
    epoch: Epoch
    sequence: Cursor


class Resized(WireModel):
    type: Literal["resized"]
    epoch: Epoch
    cols: Annotated[int, Field(ge=1, le=500)]
    rows: Annotated[int, Field(ge=1, le=300)]


class Geometry(WireModel):
    type: Literal["geometry"]
    epoch: Epoch
    sequence: Cursor
    cols: Annotated[int, Field(ge=1, le=500)]
    rows: Annotated[int, Field(ge=1, le=300)]


class TerminalStatus(WireModel):
    type: Literal["status"]
    reason: Literal[
        "requested",
        "expired",
        "idle",
        "disconnected",
        "output_limit",
        "exited",
        "provisioning_failed",
        "agent_restart",
        "agent_shutdown",
        "control_plane_lost",
        "stream_gap",
    ]


response = TypeAdapter(
    Annotated[
        Attached | ReplayComplete | Resized | Geometry | Heartbeat | TerminalStatus,
        Field(discriminator="type"),
    ]
)
