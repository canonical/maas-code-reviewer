from __future__ import annotations

from datetime import datetime

from launchpadlib.launchpad import Launchpad

from lp_ci_tools.models import Comment, MergeProposal

_SERVICE_ROOT = "https://api.launchpad.net/devel/"
_WEB_ROOT = "https://code.launchpad.net/"


def _web_url_to_api_url(url: str) -> str:
    """Convert a Launchpad web URL to its API equivalent.

    If the URL is already an API URL or doesn't match the web root,
    return it unchanged.
    """
    if url.startswith(_WEB_ROOT):
        return _SERVICE_ROOT + url[len(_WEB_ROOT) :]
    return url


class RealLaunchpadClient:
    """LaunchpadClient implementation backed by launchpadlib."""

    def __init__(self, credentials_file: str | None = None) -> None:
        self._lp = Launchpad.login_with(
            "lp-ci-tools",
            "production",
            credentials_file=credentials_file,
            version="devel",
        )

    def get_merge_proposal(self, mp_url: str) -> MergeProposal:
        api_url = _web_url_to_api_url(mp_url)
        lp_mp = self._lp.load(api_url)
        return _to_merge_proposal(lp_mp)

    def get_merge_proposals(self, project: str, status: str) -> list[MergeProposal]:
        lp_project = self._lp.load(_SERVICE_ROOT + project)
        lp_proposals = lp_project.getMergeProposals(status=status)
        return [_to_merge_proposal(lp_mp) for lp_mp in lp_proposals]

    def get_comments(self, mp_url: str) -> list[Comment]:
        lp_mp = self._lp.load(mp_url)
        return [_to_comment(lp_comment) for lp_comment in lp_mp.all_comments]

    def post_comment(self, mp_url: str, content: str, subject: str) -> None:
        lp_mp = self._lp.load(mp_url)
        lp_mp.createComment(subject=subject, content=content)

    def get_bot_username(self) -> str:
        return self._lp.me.name


def _to_merge_proposal(lp_mp: object) -> MergeProposal:
    return MergeProposal(
        url=lp_mp.web_link,  # type: ignore[attr-defined]
        api_url=lp_mp.self_link,  # type: ignore[attr-defined]
        source_git_repository=lp_mp.source_git_repository.unique_name,  # type: ignore[attr-defined]
        source_git_path=lp_mp.source_git_path,  # type: ignore[attr-defined]
        target_git_repository=lp_mp.target_git_repository.unique_name,  # type: ignore[attr-defined]
        target_git_path=lp_mp.target_git_path,  # type: ignore[attr-defined]
        status=lp_mp.queue_status,  # type: ignore[attr-defined]
        commit_message=lp_mp.commit_message or None,  # type: ignore[attr-defined]
        description=lp_mp.description or None,  # type: ignore[attr-defined]
    )


def _to_comment(lp_comment: object) -> Comment:
    date_created: datetime = lp_comment.date_created  # type: ignore[attr-defined]
    return Comment(
        author=lp_comment.author.name,  # type: ignore[attr-defined]
        body=lp_comment.message_body,  # type: ignore[attr-defined]
        date=date_created,
    )
