"""Immutable service maxima for generic artifact uploads."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ArtifactLimits:
    compressed_bytes: int = 512 * 1024 * 1024
    expanded_bytes: int = 512 * 1024 * 1024
    member_bytes: int = 64 * 1024 * 1024
    files: int = 1024

    def __post_init__(self) -> None:
        if not (
            0 < self.compressed_bytes <= 512 * 1024 * 1024
            and 0 < self.expanded_bytes <= 512 * 1024 * 1024
            and 0 < self.member_bytes <= 64 * 1024 * 1024
            and 0 < self.files <= 1024
        ):
            raise ValueError("artifact limits must be positive and within service maxima")
