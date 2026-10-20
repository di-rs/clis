"""Optional diagnostics must never invalidate a saved cleanup obligation."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cli_playground.boxie_client import SandboxView
from cli_playground.session_store import SessionBinding


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


def test_retirement_marker_preserves_first_origin(tmp_path: Path) -> None:
    from cli_playground.session_store import SessionStore

    store = SessionStore(tmp_path)
    artifact, first = uuid4(), uuid4()
    store.retire(artifact, request_id=first)
    store.retire(artifact, request_id=uuid4())
    assert store.retirement_request_id(artifact) == first
    path = store.root / "retirements" / str(artifact)
    assert path.read_text() == str(first)
    path.write_text("malformed metadata")
    assert store.retirements() == [artifact]
    assert store.retirement_request_id(artifact) is None
    path.write_bytes(b"")
    assert store.retirement_request_id(artifact) is None
