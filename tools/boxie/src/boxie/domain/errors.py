"""Stable application errors; adapters decide how to encode them."""


class BoxieError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class TerminalWriteClosed(BoxieError):
    """The upstream closed normally; its final response may still be buffered."""

    def __init__(self) -> None:
        super().__init__(503, "terminal upstream closed")
