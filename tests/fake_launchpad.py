from __future__ import annotations

from datetime import UTC

from lp_ci_tools.launchpad_client import LaunchpadClient
from lp_ci_tools.models import Comment, MergeProposal


class FakeLaunchpadClient:
    """In-memory fake that satisfies the LaunchpadClient protocol.

    Test code uses the helper methods (``add_merge_proposal``,
    ``add_comment``) to set up state, then exercises the protocol
    methods to verify behaviour.
    """

    def __init__(self, bot_username: str = "review-bot") -> None:
        self._bot_username = bot_username
        self._proposals: list[MergeProposal] = []
        # mp_url -> list of comments
        self._comments: dict[str, list[Comment]] = {}

    # ------------------------------------------------------------------
    # Helpers – used by tests to populate internal state
    # ------------------------------------------------------------------

    def add_merge_proposal(self, mp: MergeProposal) -> None:
        self._proposals.append(mp)
        self._comments.setdefault(mp.url, [])

    def add_comment(self, mp_url: str, comment: Comment) -> None:
        self._comments.setdefault(mp_url, []).append(comment)

    # ------------------------------------------------------------------
    # Protocol methods
    # ------------------------------------------------------------------

    def get_merge_proposals(self, project: str, status: str) -> list[MergeProposal]:
        return [
            mp
            for mp in self._proposals
            if mp.target_git_repository == project and mp.status == status
        ]

    def get_comments(self, mp_url: str) -> list[Comment]:
        return list(self._comments.get(mp_url, []))

    def post_comment(self, mp_url: str, content: str, subject: str) -> None:
        from datetime import datetime

        comment = Comment(
            author=self._bot_username,
            body=content,
            date=datetime.now(UTC),
        )
        self._comments.setdefault(mp_url, []).append(comment)

    def get_bot_username(self) -> str:
        return self._bot_username


def _check_protocol_compliance() -> LaunchpadClient:
    """Purely a static type-check: FakeLaunchpadClient satisfies the protocol."""
    client: LaunchpadClient = FakeLaunchpadClient()
    return client
