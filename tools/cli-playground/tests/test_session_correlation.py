"""Optional diagnostics must never invalidate a saved cleanup obligation."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from cli_playground.domain import SessionBinding
from cli_playground.sandbox import SandboxView


def test_binding_reader_accepts_optional_and_invalid_correlation() -> None:
    now = datetime.now(UTC)
    binding = SessionBinding(
        id=uuid4(),
        preview_id="preview",
        revision="revision",
        credential_hash="hash",
        pending_stop=True,
        sandbox=SandboxView(
            id=uuid4(),
            state="terminating",
            created_at=now,
            absolute_deadline=now + timedelta(hours=1),
            reason="requested",
            cleanup_pending=True,
        ),
    )
    legacy = binding.model_dump(mode="json")
    origin = uuid4()
    for value, expected in ((str(origin), origin), ("bad metadata", None), (None, None)):
        restored = SessionBinding.model_validate({**legacy, "stop_request_id": value})
        assert restored.pending_stop and restored.sandbox == binding.sandbox
        assert restored.stop_request_id == expected


@pytest.mark.integration
def test_retirement_marker_preserves_first_origin(postgres_dsn: str) -> None:
    import asyncio

    from cli_playground.session_store import SessionStore
    from cli_playground.tables import Retirement

    from .helpers import database, migrate

    migrate(postgres_dsn)
    artifact, first = uuid4(), uuid4()

    async def exercise() -> None:
        async with database(postgres_dsn) as sessions:
            store = SessionStore(sessions)
            await store.retire(artifact, request_id=first)
            await store.retire(artifact, request_id=uuid4())
        async with database(postgres_dsn) as sessions:
            store = SessionStore(sessions)
            assert await store.retirement_request_id(artifact) == first
            assert await store.retirements() == [artifact]
            async with sessions.begin() as session:
                row = await session.get(Retirement, artifact)
                assert row is not None
                row.request_id = None
            assert await store.retirements() == [artifact]
            assert await store.retirement_request_id(artifact) is None
            await store.retired(artifact)
            await store.retire(artifact, request_id=uuid4())
            assert await store.retirements() == []
            assert await store.retirement_request_id(artifact) is None

    asyncio.run(exercise())
