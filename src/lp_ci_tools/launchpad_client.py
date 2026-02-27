from __future__ import annotations

from typing import Protocol

from lp_ci_tools.models import Comment, MergeProposal


class LaunchpadClient(Protocol):
    def get_merge_proposals(self, project: str, status: str) -> list[MergeProposal]: ...

    def get_comments(self, mp_url: str) -> list[Comment]: ...

    def post_comment(self, mp_url: str, content: str, subject: str) -> None: ...

    def get_bot_username(self) -> str: ...
