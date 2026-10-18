"""Pulumi mocks shared by host and single-host composition tests."""

from typing import Any, Never

import pulumi
from pulumi.runtime.settings import reset_options


class HostMocks(pulumi.runtime.Mocks):
    def __init__(self) -> None:
        self.inputs: dict[tuple[str, str], dict[str, Any]] = {}
        self.registrations: dict[tuple[str, str], pulumi.ResourceTransformationArgs] = {}
        reset_options()
        pulumi.runtime.set_mocks(self, project="clis", stack="test")
        pulumi.runtime.register_stack_transformation(self.observe)

    def observe(self, args: pulumi.ResourceTransformationArgs) -> None:
        self.registrations[args.type_, args.name] = args

    def new_resource(self, args: pulumi.runtime.MockResourceArgs) -> tuple[str, dict[str, Any]]:
        self.inputs[args.typ, args.name] = args.inputs
        return "123", {
            **args.inputs,
            "ipAddress": "203.0.113.12",
            "aud": "test-audience",
            "stdout": "",
            "stderr": "",
            "privateKeyOpenssh": "private",
            "publicKeyOpenssh": "ssh-ed25519 YWJj",
        }

    def call(self, args: pulumi.runtime.MockCallArgs) -> Never:
        raise AssertionError(args.token)

    async def finish(self) -> None:
        for registration in list(self.registrations.values()):
            await registration.resource.urn.future()
