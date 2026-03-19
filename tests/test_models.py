from __future__ import annotations

from datetime import UTC, datetime

import pytest

from maas_code_reviewer.models import Comment
from tests.factory import make_mp


class TestDataModelsAreFrozen:
    def test_merge_proposal_is_frozen(self) -> None:
        mp = make_mp()
        with pytest.raises(AttributeError):
            mp.status = "Approved"  # type: ignore[misc]

    def test_comment_is_frozen(self) -> None:
        comment = Comment(
            author="alice",
            body="Hello",
            date=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
        )
        with pytest.raises(AttributeError):
            comment.body = "Changed"  # type: ignore[misc]
