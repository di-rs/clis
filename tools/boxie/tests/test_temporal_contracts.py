"""Workflow payloads keep the existing JSON representation across converter changes."""

import asyncio

from boxie.temporal.contracts import LifecycleFacts, LifecycleResult, StopRequest
from temporalio.api.common.v1 import Payload
from temporalio.contrib.pydantic import pydantic_data_converter


def test_temporal_pydantic_converter_reads_existing_json_payloads() -> None:
    async def exercise() -> None:
        previous = Payload(
            metadata={"encoding": b"json/plain"},
            data=b'{"absolute_deadline":120.0,"desired_stop":false,"cleaned":false,"next_deadline":null}',
        )
        (facts,) = await pydantic_data_converter.decode([previous], [LifecycleFacts])
        assert facts.model_dump() == {
            "absolute_deadline": 120.0,
            "desired_stop": False,
            "cleaned": False,
            "next_deadline": None,
        }
        encoded = await pydantic_data_converter.encode(
            [StopRequest(sandbox_id="sandbox-one", reason="expired")]
        )
        assert encoded[0].data == b'{"sandbox_id":"sandbox-one","reason":"expired"}'
        (result,) = await pydantic_data_converter.decode(
            [Payload(metadata={"encoding": b"json/plain"}, data=b'{"cleaned":true}')],
            [LifecycleResult],
        )
        assert result.model_dump() == {"cleaned": True}

    asyncio.run(exercise())
