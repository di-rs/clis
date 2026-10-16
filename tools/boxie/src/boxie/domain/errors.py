"""Stable application errors; adapters decide how to encode them."""


class BoxieError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
