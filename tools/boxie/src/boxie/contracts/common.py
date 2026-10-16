"""Common wire contracts."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

ConsumerId = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]


Positive = Annotated[int, Field(strict=True, gt=0)]


class WireModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
