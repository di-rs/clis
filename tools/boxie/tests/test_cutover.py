"""The distribution exposes only the durable lifecycle composition."""

import importlib.util

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "boxie.application.direct_lifecycle",
        "boxie.sandbox.registry",
        "boxie.sandbox.server",
    ],
)
def test_obsolete_direct_composition_is_not_importable(module: str) -> None:
    assert importlib.util.find_spec(module) is None
