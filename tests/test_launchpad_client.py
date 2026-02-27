from __future__ import annotations

from datetime import UTC, datetime

from lp_ci_tools.models import Comment, MergeProposal
from tests.fake_launchpad import FakeLaunchpadClient


def _make_mp(
    *,
    url: str = "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
    source_git_repository: str = "~user/project/+git/repo",
    source_git_path: str = "refs/heads/feature",
    target_git_repository: str = "myproject",
    target_git_path: str = "refs/heads/main",
    status: str = "Needs review",
    commit_message: str | None = None,
    description: str | None = None,
) -> MergeProposal:
    return MergeProposal(
        url=url,
        source_git_repository=source_git_repository,
        source_git_path=source_git_path,
        target_git_repository=target_git_repository,
        target_git_path=target_git_path,
        status=status,
        commit_message=commit_message,
        description=description,
    )


class TestGetMergeProposals:
    def test_empty_by_default(self) -> None:
        client = FakeLaunchpadClient()
        assert client.get_merge_proposals("myproject", "Needs review") == []

    def test_returns_matching_proposals(self) -> None:
        client = FakeLaunchpadClient()
        mp = _make_mp()
        client.add_merge_proposal(mp)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result == [mp]

    def test_filters_by_project(self) -> None:
        client = FakeLaunchpadClient()
        mp_match = _make_mp(target_git_repository="myproject")
        mp_other = _make_mp(
            url="https://code.launchpad.net/~user/other/+git/repo/+merge/2",
            target_git_repository="other-project",
        )
        client.add_merge_proposal(mp_match)
        client.add_merge_proposal(mp_other)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result == [mp_match]

    def test_filters_by_status(self) -> None:
        client = FakeLaunchpadClient()
        mp_needs_review = _make_mp(status="Needs review")
        mp_approved = _make_mp(
            url="https://code.launchpad.net/~user/project/+git/repo/+merge/2",
            status="Approved",
        )
        client.add_merge_proposal(mp_needs_review)
        client.add_merge_proposal(mp_approved)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result == [mp_needs_review]

    def test_filters_by_both_project_and_status(self) -> None:
        client = FakeLaunchpadClient()
        mp_match = _make_mp(target_git_repository="myproject", status="Needs review")
        mp_wrong_project = _make_mp(
            url="https://code.launchpad.net/~user/other/+git/repo/+merge/2",
            target_git_repository="other-project",
            status="Needs review",
        )
        mp_wrong_status = _make_mp(
            url="https://code.launchpad.net/~user/project/+git/repo/+merge/3",
            target_git_repository="myproject",
            status="Approved",
        )
        client.add_merge_proposal(mp_match)
        client.add_merge_proposal(mp_wrong_project)
        client.add_merge_proposal(mp_wrong_status)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result == [mp_match]

    def test_returns_multiple_matches(self) -> None:
        client = FakeLaunchpadClient()
        mp1 = _make_mp(
            url="https://code.launchpad.net/~user/project/+git/repo/+merge/1"
        )
        mp2 = _make_mp(
            url="https://code.launchpad.net/~user/project/+git/repo/+merge/2"
        )
        client.add_merge_proposal(mp1)
        client.add_merge_proposal(mp2)

        result = client.get_merge_proposals("myproject", "Needs review")

        assert result == [mp1, mp2]


class TestGetComments:
    def test_no_comments_returns_empty_list(self) -> None:
        client = FakeLaunchpadClient()
        assert client.get_comments("https://example.com/mp/1") == []

    def test_returns_comments_for_mp(self) -> None:
        client = FakeLaunchpadClient()
        mp = _make_mp()
        client.add_merge_proposal(mp)
        comment = Comment(
            author="alice",
            body="Looks good!",
            date=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
        )
        client.add_comment(mp.url, comment)

        result = client.get_comments(mp.url)

        assert result == [comment]

    def test_comments_are_isolated_per_mp(self) -> None:
        client = FakeLaunchpadClient()
        mp1 = _make_mp(
            url="https://code.launchpad.net/~user/project/+git/repo/+merge/1"
        )
        mp2 = _make_mp(
            url="https://code.launchpad.net/~user/project/+git/repo/+merge/2"
        )
        client.add_merge_proposal(mp1)
        client.add_merge_proposal(mp2)
        comment1 = Comment(
            author="alice",
            body="Comment on MP 1",
            date=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
        )
        comment2 = Comment(
            author="bob",
            body="Comment on MP 2",
            date=datetime(2025, 1, 15, 13, 0, 0, tzinfo=UTC),
        )
        client.add_comment(mp1.url, comment1)
        client.add_comment(mp2.url, comment2)

        assert client.get_comments(mp1.url) == [comment1]
        assert client.get_comments(mp2.url) == [comment2]

    def test_returns_copy_not_reference(self) -> None:
        client = FakeLaunchpadClient()
        mp = _make_mp()
        client.add_merge_proposal(mp)
        comment = Comment(
            author="alice",
            body="Hello",
            date=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
        )
        client.add_comment(mp.url, comment)

        result = client.get_comments(mp.url)
        result.clear()

        assert client.get_comments(mp.url) == [comment]


class TestPostComment:
    def test_post_comment_adds_comment_by_bot(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = _make_mp()
        client.add_merge_proposal(mp)

        client.post_comment(mp.url, "Nice work!", subject="Review")

        comments = client.get_comments(mp.url)
        assert len(comments) == 1
        assert comments[0].author == "ci-bot"
        assert comments[0].body == "Nice work!"

    def test_post_comment_appends_to_existing(self) -> None:
        client = FakeLaunchpadClient()
        mp = _make_mp()
        client.add_merge_proposal(mp)
        existing = Comment(
            author="alice",
            body="First comment",
            date=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
        )
        client.add_comment(mp.url, existing)

        client.post_comment(mp.url, "Bot comment", subject="Review")

        comments = client.get_comments(mp.url)
        assert len(comments) == 2
        assert comments[0] is existing
        assert comments[1].author == client.get_bot_username()


class TestGetBotUsername:
    def test_default_username(self) -> None:
        client = FakeLaunchpadClient()
        assert client.get_bot_username() == "review-bot"

    def test_custom_username(self) -> None:
        client = FakeLaunchpadClient(bot_username="my-bot")
        assert client.get_bot_username() == "my-bot"


class TestDataModelsAreFrozen:
    def test_merge_proposal_is_frozen(self) -> None:
        mp = _make_mp()
        try:
            mp.status = "Approved"  # type: ignore[misc]
            raised = False
        except AttributeError:
            raised = True
        assert raised

    def test_comment_is_frozen(self) -> None:
        comment = Comment(
            author="alice",
            body="Hello",
            date=datetime(2025, 1, 15, 12, 0, 0, tzinfo=UTC),
        )
        try:
            comment.body = "Changed"  # type: ignore[misc]
            raised = False
        except AttributeError:
            raised = True
        assert raised
