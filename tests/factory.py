from __future__ import annotations

from maas_code_reviewer.launchpad_client import _get_git_unique_name, web_url_to_api_url
from maas_code_reviewer.models import MergeProposal
from tests.fake_launchpadlib import make_fake_mp


def make_mp(
    *,
    url: str = "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
    api_url: str | None = None,
    source_git_repository: str = "~user/project/+git/repo",
    source_git_path: str = "refs/heads/feature",
    target_git_repository: str = "myproject",
    target_git_path: str = "refs/heads/main",
    status: str = "Needs review",
    commit_message: str | None = None,
    description: str | None = None,
) -> MergeProposal:
    resolved_api_url = api_url if api_url is not None else web_url_to_api_url(url)
    lp_mp = make_fake_mp(
        web_link=url,
        self_link=resolved_api_url,
        source_repo=source_git_repository,
        source_path=source_git_path,
        target_repo=target_git_repository,
        target_path=target_git_path,
        status=status,
        commit_message=commit_message,
        description=description,
    )
    return MergeProposal(
        url=lp_mp.web_link,
        api_url=lp_mp.self_link,
        source_git_repository=_get_git_unique_name(lp_mp.source_git_repository_link),
        source_git_path=lp_mp.source_git_path,
        target_git_repository=_get_git_unique_name(lp_mp.target_git_repository_link),
        target_git_path=lp_mp.target_git_path,
        status=lp_mp.queue_status,
        commit_message=lp_mp.commit_message or None,
        description=lp_mp.description or None,
        _lp_object=lp_mp,
    )
