from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from lp_ci_tools.cli import (
    REVIEW_MARKER,
    MergeProposalSummary,
    RepoTools,
    _build_parser,
    _lp_repo_url,
    _ref_to_branch,
    format_merge_proposals,
    handle_list_lp_mps,
    handle_review_mp,
    has_existing_review,
    list_merge_proposals,
    main,
    review_merge_proposal,
)
from lp_ci_tools.models import Comment
from tests.factory import make_mp
from tests.fake_git import FakeGitClient
from tests.fake_launchpad import FakeLaunchpadClient
from tests.fake_launchpadlib import FakeLaunchpad, make_fake_comment, make_fake_mp
from tests.fake_llm import FakeLLMClient, ScriptedResponse, ToolCall


class TestListMergeProposals:
    def test_no_proposals(self) -> None:
        client = FakeLaunchpadClient()

        result = list_merge_proposals(client, "myproject", "Needs review")

        assert result == []

    def test_proposal_without_review_comments(self) -> None:
        client = FakeLaunchpadClient()
        mp = make_mp()
        client.add_merge_proposal(mp)

        result = list_merge_proposals(client, "myproject", "Needs review")

        assert result == [
            MergeProposalSummary(url=mp.url, status="Needs review", last_reviewed=None)
        ]

    def test_proposal_with_review_comment(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp()
        client.add_merge_proposal(mp)
        review_date = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        client.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="[lp-ci-tools review]\n\nLooks good!",
                date=review_date,
            ),
        )

        result = list_merge_proposals(client, "myproject", "Needs review")

        assert result == [
            MergeProposalSummary(
                url=mp.url, status="Needs review", last_reviewed=review_date
            )
        ]

    def test_ignores_non_bot_comments_with_marker(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp()
        client.add_merge_proposal(mp)
        client.add_comment(
            mp.api_url,
            Comment(
                author="human-user",
                body="[lp-ci-tools review]\n\nFake review by human",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        result = list_merge_proposals(client, "myproject", "Needs review")

        assert result[0].last_reviewed is None

    def test_ignores_bot_comments_without_marker(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp()
        client.add_merge_proposal(mp)
        client.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="Just a regular comment, no marker",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        result = list_merge_proposals(client, "myproject", "Needs review")

        assert result[0].last_reviewed is None

    def test_uses_latest_review_date(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp()
        client.add_merge_proposal(mp)
        early = datetime(2025, 6, 10, 8, 0, 0, tzinfo=UTC)
        late = datetime(2025, 6, 15, 12, 0, 0, tzinfo=UTC)
        client.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="[lp-ci-tools review]\n\nFirst review",
                date=early,
            ),
        )
        client.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="[lp-ci-tools review]\n\nSecond review",
                date=late,
            ),
        )

        result = list_merge_proposals(client, "myproject", "Needs review")

        assert result[0].last_reviewed == late

    def test_multiple_proposals_mixed_review_state(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp1 = make_mp(url="https://code.launchpad.net/~user/project/+git/repo/+merge/1")
        mp2 = make_mp(url="https://code.launchpad.net/~user/project/+git/repo/+merge/2")
        client.add_merge_proposal(mp1)
        client.add_merge_proposal(mp2)
        review_date = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        client.add_comment(
            mp1.api_url,
            Comment(
                author="ci-bot",
                body="[lp-ci-tools review]\n\nOK",
                date=review_date,
            ),
        )

        result = list_merge_proposals(client, "myproject", "Needs review")

        assert len(result) == 2
        assert result[0].last_reviewed == review_date
        assert result[1].last_reviewed is None

    def test_marker_must_be_at_start_of_body(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp()
        client.add_merge_proposal(mp)
        client.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="Some preamble [lp-ci-tools review]\n\nContent",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        result = list_merge_proposals(client, "myproject", "Needs review")

        assert result[0].last_reviewed is None


class TestFormatMergeProposals:
    def test_empty_list(self) -> None:
        assert format_merge_proposals([]) == ""

    def test_single_unreviewed(self) -> None:
        summaries = [
            MergeProposalSummary(
                url="https://code.launchpad.net/~user/project/+git/repo/+merge/1",
                status="Needs review",
                last_reviewed=None,
            )
        ]

        output = format_merge_proposals(summaries)

        assert output == (
            "https://code.launchpad.net/~user/project/+git/repo/+merge/1"
            " Needs review never"
        )

    def test_single_reviewed(self) -> None:
        review_date = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        summaries = [
            MergeProposalSummary(
                url="https://code.launchpad.net/~user/project/+git/repo/+merge/1",
                status="Needs review",
                last_reviewed=review_date,
            )
        ]

        output = format_merge_proposals(summaries)

        assert output == (
            "https://code.launchpad.net/~user/project/+git/repo/+merge/1"
            f" Needs review {review_date.isoformat()}"
        )

    def test_multiple_proposals(self) -> None:
        review_date = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        summaries = [
            MergeProposalSummary(
                url="https://code.launchpad.net/~user/project/+git/repo/+merge/1",
                status="Needs review",
                last_reviewed=review_date,
            ),
            MergeProposalSummary(
                url="https://code.launchpad.net/~user/project/+git/repo/+merge/2",
                status="Needs review",
                last_reviewed=None,
            ),
        ]

        output = format_merge_proposals(summaries)

        lines = output.split("\n")
        assert len(lines) == 2
        assert lines[0].endswith(review_date.isoformat())
        assert lines[1].endswith("never")


class TestBuildParser:
    def test_list_lp_mps_defaults_status_to_needs_review(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["list-lp-mps", "myproject"])
        assert args.command == "list-lp-mps"
        assert args.status == "Needs review"
        assert args.project == "myproject"
        assert args.launchpad_credentials is None

    def test_list_lp_mps_parses_explicit_status(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["list-lp-mps", "--status", "Approved", "myproject"])
        assert args.command == "list-lp-mps"
        assert args.status == "Approved"
        assert args.project == "myproject"

    def test_list_lp_mps_parses_credentials(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(
            [
                "list-lp-mps",
                "--launchpad-credentials",
                "/path/to/creds",
                "--status",
                "Approved",
                "myproject",
            ]
        )
        assert args.launchpad_credentials == "/path/to/creds"

    def test_no_subcommand_gives_none(self) -> None:
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.command is None

    def test_review_mp_parses_mp_url(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(
            [
                "review-mp",
                "-g",
                str(key_file),
                "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
            ]
        )
        assert args.command == "review-mp"
        assert (
            args.mp_url == "https://code.launchpad.net/~user/project/+git/repo/+merge/1"
        )
        assert args.dry_run is False
        assert args.launchpad_credentials is None
        assert args.gemini_api_key_file == str(key_file)
        assert args.model == "gemini-3-flash-preview"

    def test_review_mp_parses_dry_run(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(
            [
                "review-mp",
                "--dry-run",
                "-g",
                str(key_file),
                "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
            ]
        )
        assert args.dry_run is True

    def test_review_mp_parses_launchpad_credentials(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(
            [
                "review-mp",
                "--launchpad-credentials",
                "/path/to/creds",
                "-g",
                str(key_file),
                "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
            ]
        )
        assert args.launchpad_credentials == "/path/to/creds"

    def test_review_mp_parses_gemini_api_key_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "gemini.key"
        key_file.write_text("my-secret-key\n")
        parser = _build_parser()
        args = parser.parse_args(
            [
                "review-mp",
                "--gemini-api-key-file",
                str(key_file),
                "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
            ]
        )
        assert args.gemini_api_key_file == str(key_file)

    def test_review_mp_model_defaults_to_gemini_3_flash_preview(
        self, tmp_path: Path
    ) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(
            [
                "review-mp",
                "-g",
                str(key_file),
                "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
            ]
        )
        assert args.model == "gemini-3-flash-preview"

    def test_review_mp_parses_model(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(
            [
                "review-mp",
                "--model",
                "gemini-2.5-pro",
                "-g",
                str(key_file),
                "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
            ]
        )
        assert args.model == "gemini-2.5-pro"


class TestMain:
    def test_no_command_exits_with_code_1(self) -> None:
        with pytest.raises(SystemExit) as exc_info:
            main([])
        assert exc_info.value.code == 1

    def test_list_lp_mps_delegates_to_handle_list_lp_mps(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        fake_lp = FakeLaunchpad(bot_username="review-bot")
        lp_mp = make_fake_mp(status="Needs review")
        fake_lp.add_merge_proposal("myproject", lp_mp)
        with fake_lp.patch_login_with():
            main(["list-lp-mps", "--status", "Needs review", "myproject"])
        captured = capsys.readouterr()
        assert lp_mp.web_link in captured.out

    def test_review_mp_command_delegates_to_handle_review_mp(
        self, tmp_path: Path
    ) -> None:
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient([ScriptedResponse(text="Looks great.")])

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        with (
            patch("lp_ci_tools.cli.LaunchpadClient", return_value=lp),
            patch("lp_ci_tools.cli.GitClient", return_value=git),
            patch("lp_ci_tools.cli.GeminiClient", return_value=llm),
        ):
            main(
                [
                    "review-mp",
                    "--gemini-api-key-file",
                    str(api_key_file),
                    mp.url,
                ]
            )

        comments = lp.get_comments_for(mp.api_url)
        assert len(comments) == 1
        assert "Looks great." in comments[0].body


class TestHandleListLpMps:
    def test_prints_output(self, capsys: pytest.CaptureFixture[str]) -> None:
        fake_lp = FakeLaunchpad(bot_username="review-bot")
        lp_mp = make_fake_mp(status="Needs review")
        fake_lp.add_merge_proposal("myproject", lp_mp)
        with fake_lp.patch_login_with():
            args = _build_parser().parse_args(
                ["list-lp-mps", "--status", "Needs review", "myproject"]
            )
            handle_list_lp_mps(args)
        captured = capsys.readouterr()
        assert lp_mp.web_link in captured.out
        assert "Needs review" in captured.out
        assert "never" in captured.out

    def test_no_output_when_empty(self, capsys: pytest.CaptureFixture[str]) -> None:
        fake_lp = FakeLaunchpad()
        fake_lp.add_project("myproject")
        with fake_lp.patch_login_with():
            args = _build_parser().parse_args(
                ["list-lp-mps", "--status", "Needs review", "myproject"]
            )
            handle_list_lp_mps(args)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_passes_credentials(self) -> None:
        fake_lp = FakeLaunchpad()
        fake_lp.add_project("myproject")
        with fake_lp.patch_login_with():
            args = _build_parser().parse_args(
                [
                    "list-lp-mps",
                    "--launchpad-credentials",
                    "/path/to/creds",
                    "--status",
                    "Needs review",
                    "myproject",
                ]
            )
            handle_list_lp_mps(args)
        assert fake_lp.credentials_file == "/path/to/creds"

    def test_shows_last_review_date(self, capsys: pytest.CaptureFixture[str]) -> None:
        fake_lp = FakeLaunchpad(bot_username="ci-bot")
        lp_mp = make_fake_mp(status="Needs review")
        fake_lp.add_merge_proposal("myproject", lp_mp)
        review_date = datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC)
        fake_lp.add_comment(
            lp_mp.web_link,
            make_fake_comment(
                author="ci-bot",
                body="[lp-ci-tools review]\n\nLooks good!",
                date=review_date,
            ),
        )
        with fake_lp.patch_login_with():
            args = _build_parser().parse_args(
                ["list-lp-mps", "--status", "Needs review", "myproject"]
            )
            handle_list_lp_mps(args)
        captured = capsys.readouterr()
        assert lp_mp.web_link in captured.out
        assert review_date.isoformat() in captured.out


class TestHandleReviewMp:
    def test_posts_review_comment(self, tmp_path: Path) -> None:
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient([ScriptedResponse(text="Looks great.")])

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        with (
            patch("lp_ci_tools.cli.LaunchpadClient", return_value=lp),
            patch("lp_ci_tools.cli.GitClient", return_value=git),
            patch("lp_ci_tools.cli.GeminiClient", return_value=llm),
        ):
            args = _build_parser().parse_args(
                ["review-mp", "--gemini-api-key-file", str(api_key_file), mp.url]
            )
            handle_review_mp(args)

        comments = lp.get_comments_for(mp.api_url)
        assert len(comments) == 1
        assert "Looks great." in comments[0].body

    def test_prints_already_reviewed(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)
        lp.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="[lp-ci-tools review]\n\nAlready done.",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        llm = FakeLLMClient()

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        with (
            patch("lp_ci_tools.cli.LaunchpadClient", return_value=lp),
            patch("lp_ci_tools.cli.GitClient", return_value=git),
            patch("lp_ci_tools.cli.GeminiClient", return_value=llm),
        ):
            args = _build_parser().parse_args(
                ["review-mp", "--gemini-api-key-file", str(api_key_file), mp.url]
            )
            handle_review_mp(args)

        captured = capsys.readouterr()
        assert "Already reviewed, skipping." in captured.out

    def test_dry_run_prints_review_without_posting(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient([ScriptedResponse(text="Dry run review.")])

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        with (
            patch("lp_ci_tools.cli.LaunchpadClient", return_value=lp),
            patch("lp_ci_tools.cli.GitClient", return_value=git),
            patch("lp_ci_tools.cli.GeminiClient", return_value=llm),
        ):
            args = _build_parser().parse_args(
                [
                    "review-mp",
                    "--dry-run",
                    "--gemini-api-key-file",
                    str(api_key_file),
                    mp.url,
                ]
            )
            handle_review_mp(args)

        captured = capsys.readouterr()
        assert "Dry run review." in captured.out
        assert len(lp.get_comments_for(mp.api_url)) == 0


class TestLpRepoUrl:
    def test_prepends_git_base(self) -> None:
        result = _lp_repo_url("~user/project/+git/repo")
        assert result == "https://git.launchpad.net/~user/project/+git/repo"

    def test_plain_name(self) -> None:
        result = _lp_repo_url("myproject")
        assert result == "https://git.launchpad.net/myproject"


class TestRefToBranch:
    def test_strips_refs_heads_prefix(self) -> None:
        assert _ref_to_branch("refs/heads/feature") == "feature"

    def test_strips_refs_heads_with_slashes(self) -> None:
        assert _ref_to_branch("refs/heads/user/my-feature") == "user/my-feature"

    def test_returns_unchanged_without_prefix(self) -> None:
        assert _ref_to_branch("main") == "main"

    def test_returns_unchanged_for_partial_prefix(self) -> None:
        assert _ref_to_branch("refs/tags/v1.0") == "refs/tags/v1.0"


class TestHasExistingReview:
    def test_returns_false_when_no_comments(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp()
        client.add_merge_proposal(mp)

        assert has_existing_review(client, mp) is False

    def test_returns_true_when_bot_review_exists(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp()
        client.add_merge_proposal(mp)
        client.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="[lp-ci-tools review]\n\nLooks good!",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        assert has_existing_review(client, mp) is True

    def test_returns_false_for_non_bot_comment_with_marker(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp()
        client.add_merge_proposal(mp)
        client.add_comment(
            mp.api_url,
            Comment(
                author="human-user",
                body="[lp-ci-tools review]\n\nFake review",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        assert has_existing_review(client, mp) is False

    def test_returns_false_for_bot_comment_without_marker(self) -> None:
        client = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp()
        client.add_merge_proposal(mp)
        client.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="Just a regular comment",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        assert has_existing_review(client, mp) is False


def _setup_repos(
    tmp_path: Path,
    git: FakeGitClient,
    *,
    target_files: dict[str, str] | None = None,
    source_files: dict[str, str] | None = None,
) -> tuple[Path, Path]:
    """Create a target and source repo for review tests.

    Returns (target_repo_path, source_repo_path).
    The source repo is cloned from target, with a ``feature`` branch
    containing the source changes.
    """
    if target_files is None:
        target_files = {"file.txt": "original\n"}
    if source_files is None:
        source_files = {"file.txt": "modified\n"}

    target_repo = tmp_path / "target"
    git.create_repo(target_repo)
    git.add_commit(target_repo, target_files, message="init")

    source_repo = tmp_path / "source"
    git.clone(str(target_repo), source_repo, "main")
    git.create_branch(source_repo, "feature")
    git.checkout(source_repo, "feature")
    git.add_commit(source_repo, source_files, message="feature work")

    return target_repo, source_repo


class TestReviewMergeProposal:
    def test_returns_review_body(self, tmp_path: Path) -> None:
        """MP exists, not yet reviewed — review body is returned."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient([ScriptedResponse(text="LGTM, no issues found.")])

        result = review_merge_proposal(lp, git, llm, mp.url)

        assert result is not None
        assert result.startswith(REVIEW_MARKER)
        assert "LGTM, no issues found." in result
        # Posting is the caller's responsibility — nothing posted here
        assert lp.get_comments_for(mp.api_url) == []

    def test_already_reviewed_mp_returns_none(self, tmp_path: Path) -> None:
        """An MP with an existing review is skipped."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)
        lp.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="[lp-ci-tools review]\n\nPrevious review.",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        llm = FakeLLMClient()  # no responses needed — should not be called

        result = review_merge_proposal(lp, git, llm, mp.url)

        assert result is None
        # No new comment should have been posted
        comments = lp.get_comments_for(mp.api_url)
        assert len(comments) == 1  # only the pre-existing one

    def test_returns_review_body_without_posting(self, tmp_path: Path) -> None:
        """review_merge_proposal returns the body without posting a comment."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient([ScriptedResponse(text="Some review text.")])

        result = review_merge_proposal(lp, git, llm, mp.url)

        assert result is not None
        assert "Some review text." in result
        # Posting is the caller's responsibility — nothing posted here
        comments = lp.get_comments_for(mp.api_url)
        assert len(comments) == 0

    def test_diff_is_passed_to_llm(self, tmp_path: Path) -> None:
        """The diff content is included in the prompt sent to the LLM."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(
            tmp_path,
            git,
            target_files={"code.py": "def hello():\n    pass\n"},
            source_files={"code.py": "def hello():\n    print('hi')\n"},
        )

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient([ScriptedResponse(text="Nice change.")])

        review_merge_proposal(lp, git, llm, mp.url)

        prompt = llm._client.received_prompts[0]
        assert "pass" in prompt
        assert "print" in prompt

    def test_description_is_passed_to_llm(self, tmp_path: Path) -> None:
        """The MP description is included in the prompt."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
            description="Fix the widget rendering bug",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient([ScriptedResponse(text="Looks good.")])

        review_merge_proposal(lp, git, llm, mp.url)

        prompt = llm._client.received_prompts[0]
        assert "Fix the widget rendering bug" in prompt

    def test_commit_message_used_when_no_description(self, tmp_path: Path) -> None:
        """Falls back to commit_message when description is None."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
            description=None,
            commit_message="Refactor auth module",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient([ScriptedResponse(text="OK.")])

        review_merge_proposal(lp, git, llm, mp.url)

        prompt = llm._client.received_prompts[0]
        assert "Refactor auth module" in prompt

    def test_tools_provided_to_llm_can_read_files(self, tmp_path: Path) -> None:
        """The read_file tool provided to the LLM can read from the repo."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(
            tmp_path,
            git,
            target_files={"AGENTS.md": "# Rules\nBe nice.\n", "file.txt": "a\n"},
            source_files={"file.txt": "b\n"},
        )

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        from tests.fake_llm import ToolCall

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="Reviewed with context.",
                    tool_calls=[
                        ToolCall(name="read_file", args={"path": "AGENTS.md"}),
                    ],
                ),
            ]
        )

        result = review_merge_proposal(lp, git, llm, mp.url)

        assert result is not None
        assert "Reviewed with context." in result

    def test_read_file_tool_returns_error_for_missing_file(
        self, tmp_path: Path
    ) -> None:
        """The read_file tool returns an error string for nonexistent files."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="File missing, moving on.",
                    tool_calls=[
                        ToolCall(
                            name="read_file",
                            args={"path": "nonexistent.py"},
                        ),
                    ],
                ),
            ]
        )

        result = review_merge_proposal(lp, git, llm, mp.url)

        assert result is not None
        assert "File missing, moving on." in result

    def test_tools_provided_to_llm_can_list_directory(self, tmp_path: Path) -> None:
        """The list_directory tool provided to the LLM can list repo contents."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(
            tmp_path,
            git,
            target_files={"src/main.py": "print('hi')\n", "src/utils.py": "pass\n"},
            source_files={"src/main.py": "print('hello')\n"},
        )

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="Checked src directory.",
                    tool_calls=[
                        ToolCall(name="list_directory", args={"path": "src"}),
                    ],
                ),
            ]
        )

        result = review_merge_proposal(lp, git, llm, mp.url)

        assert result is not None
        assert "Checked src directory." in result

    def test_list_directory_tool_returns_error_for_missing_dir(
        self, tmp_path: Path
    ) -> None:
        """The list_directory tool returns an error for nonexistent directories."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="Dir not found, continuing.",
                    tool_calls=[
                        ToolCall(
                            name="list_directory",
                            args={"path": "nonexistent"},
                        ),
                    ],
                ),
            ]
        )

        result = review_merge_proposal(lp, git, llm, mp.url)

        assert result is not None
        assert "Dir not found, continuing." in result

    def test_review_comment_has_correct_format(self, tmp_path: Path) -> None:
        """The posted comment starts with the marker, then blank line, then review."""
        git = FakeGitClient()
        target_repo, source_repo = _setup_repos(tmp_path, git)

        lp = FakeLaunchpadClient(bot_username="ci-bot")
        mp = make_mp(
            source_git_repository=str(source_repo),
            source_git_path="refs/heads/feature",
            target_git_repository=str(target_repo),
            target_git_path="refs/heads/main",
        )
        lp.add_merge_proposal(mp)

        llm = FakeLLMClient([ScriptedResponse(text="Review body here.")])

        result = review_merge_proposal(lp, git, llm, mp.url)

        assert result == "[lp-ci-tools review]\n\nReview body here."


class TestRepoTools:
    def test_read_file_returns_content(self, tmp_path: Path) -> None:
        """read_file returns the content of a file inside the repository."""
        git = FakeGitClient()
        git.create_repo(tmp_path)
        git.add_commit(tmp_path, {"notes.txt": "hello\n"}, message="init")

        tools = RepoTools(tmp_path, git)

        assert tools.read_file("notes.txt") == "hello\n"

    def test_read_file_returns_error_for_missing_file(self, tmp_path: Path) -> None:
        """read_file returns an error string when the file does not exist."""
        git = FakeGitClient()
        git.create_repo(tmp_path)
        git.add_commit(tmp_path, {"file.txt": "x\n"}, message="init")

        tools = RepoTools(tmp_path, git)

        assert tools.read_file("missing.txt") == "Error: file not found: missing.txt"

    def test_read_file_rejects_dotdot_traversal(self, tmp_path: Path) -> None:
        """read_file refuses a relative path that escapes the repository."""
        repo = tmp_path / "repo"
        repo.mkdir()
        secret = tmp_path / "credentials.txt"
        secret.write_text("super-secret-api-key\n")

        git = FakeGitClient()
        git.create_repo(repo)
        git.add_commit(repo, {"file.txt": "safe\n"}, message="init")

        tools = RepoTools(repo, git)
        result = tools.read_file("../credentials.txt")

        assert "Error: path outside repository" in result
        assert "super-secret-api-key" not in result

    def test_read_file_rejects_absolute_path(self, tmp_path: Path) -> None:
        """read_file refuses an absolute path pointing outside the repository."""
        repo = tmp_path / "repo"
        repo.mkdir()
        secret = tmp_path / "credentials.txt"
        secret.write_text("super-secret-api-key\n")

        git = FakeGitClient()
        git.create_repo(repo)
        git.add_commit(repo, {"file.txt": "safe\n"}, message="init")

        tools = RepoTools(repo, git)
        result = tools.read_file(str(secret))

        assert "Error: path outside repository" in result
        assert "super-secret-api-key" not in result

    def test_list_directory_returns_sorted_entries(self, tmp_path: Path) -> None:
        """list_directory returns a sorted newline-joined list of entry names."""
        git = FakeGitClient()
        git.create_repo(tmp_path)
        git.add_commit(
            tmp_path,
            {"src/beta.py": "pass\n", "src/alpha.py": "pass\n"},
            message="init",
        )

        tools = RepoTools(tmp_path, git)

        assert tools.list_directory("src") == "alpha.py\nbeta.py"

    def test_list_directory_returns_error_for_missing_dir(self, tmp_path: Path) -> None:
        """list_directory returns an error string when the directory does not exist."""
        git = FakeGitClient()
        git.create_repo(tmp_path)
        git.add_commit(tmp_path, {"file.txt": "x\n"}, message="init")

        tools = RepoTools(tmp_path, git)

        assert (
            tools.list_directory("nonexistent")
            == "Error: directory not found: nonexistent"
        )

    def test_list_directory_rejects_dotdot_traversal(self, tmp_path: Path) -> None:
        """list_directory refuses a relative path that escapes the repository."""
        repo = tmp_path / "repo"
        repo.mkdir()

        git = FakeGitClient()
        git.create_repo(repo)
        git.add_commit(repo, {"file.txt": "safe\n"}, message="init")

        tools = RepoTools(repo, git)
        result = tools.list_directory("../..")

        assert "Error: path outside repository" in result

    def test_list_directory_rejects_absolute_path(self, tmp_path: Path) -> None:
        """list_directory refuses an absolute path pointing outside the repository."""
        repo = tmp_path / "repo"
        repo.mkdir()

        git = FakeGitClient()
        git.create_repo(repo)
        git.add_commit(repo, {"file.txt": "safe\n"}, message="init")

        tools = RepoTools(repo, git)
        result = tools.list_directory(str(tmp_path))

        assert "Error: path outside repository" in result
