from __future__ import annotations

from datetime import UTC, datetime

from lp_ci_tools.real_launchpad_client import RealLaunchpadClient, _web_url_to_api_url
from tests.fake_launchpadlib import (
    FakeLaunchpad,
    make_fake_comment,
    make_fake_mp,
)


def _make_client(
    fake_lp: FakeLaunchpad, credentials_file: str | None = None
) -> RealLaunchpadClient:
    with fake_lp.patch_login_with():
        return RealLaunchpadClient(credentials_file=credentials_file)


class TestGetMergeProposals:
    def test_empty_project(self) -> None:
        fake_lp = FakeLaunchpad()
        fake_lp.add_project("myproject")
        client = _make_client(fake_lp)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result == []

    def test_returns_matching_proposals(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp(status="Needs review")
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert len(result) == 1
        assert result[0].url == mp.web_link
        assert result[0].api_url == mp.self_link
        assert result[0].source_git_repository == "~user/project/+git/repo"
        assert result[0].source_git_path == "refs/heads/feature"
        assert result[0].target_git_repository == "~user/project/+git/repo"
        assert result[0].target_git_path == "refs/heads/main"
        assert result[0].status == "Needs review"

    def test_filters_by_status(self) -> None:
        fake_lp = FakeLaunchpad()
        mp_review = make_fake_mp(
            web_link="https://code.launchpad.net/~user/project/+git/repo/+merge/1",
            status="Needs review",
        )
        mp_approved = make_fake_mp(
            web_link="https://code.launchpad.net/~user/project/+git/repo/+merge/2",
            status="Approved",
        )
        fake_lp.add_merge_proposal("myproject", mp_review)
        fake_lp.add_merge_proposal("myproject", mp_approved)
        client = _make_client(fake_lp)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert len(result) == 1
        assert result[0].url == mp_review.web_link

    def test_commit_message_none_when_empty(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp(commit_message="")
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result[0].commit_message is None

    def test_description_none_when_empty(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp(description="")
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result[0].description is None

    def test_commit_message_preserved_when_set(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp(commit_message="Fix the thing")
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result[0].commit_message == "Fix the thing"

    def test_description_preserved_when_set(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp(description="A detailed description")
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result[0].description == "A detailed description"


class TestGetMergeProposal:
    def test_returns_merge_proposal_by_api_url(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp(status="Needs review")
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_merge_proposal(mp.self_link)

        assert result.url == mp.web_link
        assert result.api_url == mp.self_link
        assert result.source_git_repository == "~user/project/+git/repo"
        assert result.source_git_path == "refs/heads/feature"
        assert result.target_git_repository == "~user/project/+git/repo"
        assert result.target_git_path == "refs/heads/main"
        assert result.status == "Needs review"

    def test_preserves_commit_message(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp(commit_message="Fix the thing")
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_merge_proposal(mp.self_link)

        assert result.commit_message == "Fix the thing"

    def test_preserves_description(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp(description="A detailed description")
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_merge_proposal(mp.self_link)

        assert result.description == "A detailed description"

    def test_returns_merge_proposal_by_web_url(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp(status="Needs review")
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_merge_proposal(mp.web_link)

        assert result.url == mp.web_link
        assert result.api_url == mp.self_link


class TestWebUrlToApiUrl:
    def test_converts_web_url(self) -> None:
        url = "https://code.launchpad.net/~user/project/+git/repo/+merge/123"
        result = _web_url_to_api_url(url)
        assert (
            result
            == "https://api.launchpad.net/devel/~user/project/+git/repo/+merge/123"
        )

    def test_leaves_api_url_unchanged(self) -> None:
        url = "https://api.launchpad.net/devel/~user/project/+git/repo/+merge/123"
        result = _web_url_to_api_url(url)
        assert result == url

    def test_leaves_unknown_url_unchanged(self) -> None:
        url = "https://example.com/something"
        result = _web_url_to_api_url(url)
        assert result == url


class TestGetComments:
    def test_no_comments(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp()
        fake_lp.add_merge_proposal("myproject", mp)
        client = _make_client(fake_lp)

        result = client.get_comments(mp.self_link)

        assert result == []

    def test_returns_comments(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp()
        fake_lp.add_merge_proposal("myproject", mp)
        date = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        comment = make_fake_comment(author="alice", body="Looks good!", date=date)
        fake_lp.add_comment(mp.web_link, comment)
        client = _make_client(fake_lp)

        result = client.get_comments(mp.self_link)

        assert len(result) == 1
        assert result[0].author == "alice"
        assert result[0].body == "Looks good!"
        assert result[0].date == date

    def test_multiple_comments(self) -> None:
        fake_lp = FakeLaunchpad()
        mp = make_fake_mp()
        fake_lp.add_merge_proposal("myproject", mp)
        c1 = make_fake_comment(author="alice", body="First")
        c2 = make_fake_comment(author="bob", body="Second")
        fake_lp.add_comment(mp.web_link, c1)
        fake_lp.add_comment(mp.web_link, c2)
        client = _make_client(fake_lp)

        result = client.get_comments(mp.self_link)

        assert len(result) == 2
        assert result[0].author == "alice"
        assert result[1].author == "bob"


class TestGetBotUsername:
    def test_returns_bot_username(self) -> None:
        fake_lp = FakeLaunchpad(bot_username="ci-bot")
        client = _make_client(fake_lp)

        assert client.get_bot_username() == "ci-bot"

    def test_default_username(self) -> None:
        fake_lp = FakeLaunchpad()
        client = _make_client(fake_lp)

        assert client.get_bot_username() == "review-bot"


class TestCredentials:
    def test_credentials_file_is_recorded(self) -> None:
        fake_lp = FakeLaunchpad()
        fake_lp.add_project("myproject")
        _make_client(fake_lp, credentials_file="/path/to/creds")

        assert fake_lp.credentials_file == "/path/to/creds"

    def test_credentials_file_none_when_omitted(self) -> None:
        fake_lp = FakeLaunchpad()
        fake_lp.add_project("myproject")
        _make_client(fake_lp)

        assert fake_lp.credentials_file is None
