"""Domain decisions stay synchronous and independent of persistence."""

from uuid import uuid4

import pytest
from cli_playground import models


def test_stale_build_cannot_replace_current_preview() -> None:
    from cli_playground.domain import ArtifactConflictError, check_build

    current = models.PreviewRecord(
        pr=123,
        expires_at=100,
        build=models.BuildStatus(
            sha="a" * 40, run_id=20, run_number=2, run_attempt=1, state="ready"
        ),
    )
    old = models.BuildIdentity(sha="b" * 40, run_id=10, run_number=1, run_attempt=1)
    with pytest.raises(ArtifactConflictError, match="stale"):
        check_build(current, False, old)


def test_closed_preview_rejects_first_build() -> None:
    from cli_playground.domain import ArtifactConflictError, check_build

    with pytest.raises(ArtifactConflictError, match="closed"):
        check_build(None, True, None)


def test_domain_attachment_rejects_stale_completion() -> None:
    from datetime import UTC, datetime, timedelta

    from cli_playground.domain import (
        SessionBinding,
        cancel_attachment,
        claim_attachment,
        complete_attachment,
    )
    from cli_playground.sandbox import SandboxView

    view = SandboxView(
        id=uuid4(),
        state="ready",
        created_at=datetime.now(UTC),
        absolute_deadline=datetime.now(UTC) + timedelta(minutes=5),
        reason=None,
        cleanup_pending=False,
    )
    binding = SessionBinding(
        id=uuid4(), preview_id="preview", revision="a" * 40, sandbox=view, credential_hash="hash"
    )
    claim_attachment(binding, 1)
    first = binding.attachment_claim
    claim_attachment(binding, 2)
    complete_attachment(binding, view, binding.attachment_claim)
    with pytest.raises(LookupError, match="superseded"):
        complete_attachment(binding, view, first)
    cancel_attachment(binding, first)
    assert binding.attached and not binding.pending_stop
