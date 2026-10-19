"""Credentials are injected by composition; all comparisons are constant-time."""

import hmac
from collections.abc import Mapping

from boxie.domain.errors import BoxieError
from boxie.domain.models import Principal


class ConsumerAuth:
    def __init__(self, credentials: Mapping[str, bytes]) -> None:
        self._credentials = dict(credentials)

    def resolve(self, header: str) -> Principal:
        candidate = header.encode()
        found: str | None = None
        for consumer, secret in self._credentials.items():
            if hmac.compare_digest(candidate, b"Bearer " + secret):
                found = consumer
        if found is None:
            raise BoxieError(401, "invalid consumer credential")
        return Principal(found)
