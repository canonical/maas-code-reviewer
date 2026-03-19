from __future__ import annotations

from datetime import UTC, datetime

from maas_code_reviewer.launchpad_client import LaunchpadClient
from maas_code_reviewer.models import Comment, MergeProposal


class FakeLaunchpadClient(LaunchpadClient):
    """In-memory fake that inherits from LaunchpadClient.

    Test code uses the helper methods (``add_merge_proposal``,
    ``add_comment``) to set up state, then exercises the methods
    to verify behaviour.
    """

    def __init__(self, bot_username: str = "review-bot") -> None:
        # Deliberately skip super().__init__() — we don't want a real
        # launchpadlib connection.
        self._bot_username = bot_username
        self._proposals: list[MergeProposal] = []
        # mp api_url -> list of comments
        self._comments: dict[str, list[Comment]] = {}

    # ------------------------------------------------------------------
    # Helpers – used by tests to populate internal state
    # ------------------------------------------------------------------

    def add_merge_proposal(self, mp: MergeProposal) -> None:
        self._proposals.append(mp)
        self._comments.setdefault(mp.api_url, [])

    def add_comment(self, mp_api_url: str, comment: Comment) -> None:
        self._comments.setdefault(mp_api_url, []).append(comment)

    # ------------------------------------------------------------------
    # Overridden methods
    # ------------------------------------------------------------------

    def get_merge_proposal(self, mp_url: str) -> MergeProposal:
        for mp in self._proposals:
            if mp.url == mp_url or mp.api_url == mp_url:
                return mp

    def get_merge_proposals(self, project: str, status: str) -> list[MergeProposal]:
        return [
            mp
            for mp in self._proposals
            if mp.target_git_repository == project and mp.status == status
        ]

    def get_comments(self, mp: MergeProposal) -> list[Comment]:
        return list(self._comments.get(mp.api_url, []))

    def post_comment(self, mp: MergeProposal, content: str, subject: str) -> None:
        comment = Comment(
            author=self._bot_username,
            body=content,
            date=datetime.now(UTC),
        )
        self._comments.setdefault(mp.api_url, []).append(comment)

    def get_bot_username(self) -> str:
        return self._bot_username

    # ------------------------------------------------------------------
    # Test inspection helpers
    # ------------------------------------------------------------------

    def get_comments_for(self, mp_api_url: str) -> list[Comment]:
        """Return comments for an MP by api_url, for use in test assertions."""
        return list(self._comments.get(mp_api_url, []))
