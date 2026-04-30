# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from maas_code_reviewer.cli import (
    REVIEW_MARKER,
    MergeProposalSummary,
    _build_parser,
    _get_gemini_api_key,
    _lp_repo_url,
    _ref_to_branch,
    format_merge_proposals,
    handle_list_lp_mps,
    handle_review_diff,
    handle_review_mp,
    handle_review_pr,
    has_existing_review,
    list_merge_proposals,
    main,
    review_merge_proposal,
)
from maas_code_reviewer.metrics import ReviewMetrics
from maas_code_reviewer.models import Comment
from maas_code_reviewer.reviewer import REVIEW_PREAMBLE
from tests.factory import make_mp
from tests.fake_git import FakeGitClient
from tests.fake_github import FakeGitHubClient
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
                body="[maas-code-reviewer review]\n\nLooks good!",
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
                body="[maas-code-reviewer review]\n\nFake review by human",
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
                body="[maas-code-reviewer review]\n\nFirst review",
                date=early,
            ),
        )
        client.add_comment(
            mp.api_url,
            Comment(
                author="ci-bot",
                body="[maas-code-reviewer review]\n\nSecond review",
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
                body="[maas-code-reviewer review]\n\nOK",
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
                body="Some preamble [maas-code-reviewer review]\n\nContent",
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

    def test_review_mp_metrics_defaults_to_none(self, tmp_path: Path) -> None:
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
        assert args.metrics is None

    def test_review_mp_parses_metrics(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(
            [
                "review-mp",
                "-g",
                str(key_file),
                "--metrics",
                "/tmp/m.json",
                "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
            ]
        )
        assert args.metrics == "/tmp/m.json"


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
            patch("maas_code_reviewer.cli.LaunchpadClient", return_value=lp),
            patch("maas_code_reviewer.cli.GitClient", return_value=git),
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
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
                body="[maas-code-reviewer review]\n\nLooks good!",
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
            patch("maas_code_reviewer.cli.LaunchpadClient", return_value=lp),
            patch("maas_code_reviewer.cli.GitClient", return_value=git),
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
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
                body="[maas-code-reviewer review]\n\nAlready done.",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        llm = FakeLLMClient()

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        with (
            patch("maas_code_reviewer.cli.LaunchpadClient", return_value=lp),
            patch("maas_code_reviewer.cli.GitClient", return_value=git),
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
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
            patch("maas_code_reviewer.cli.LaunchpadClient", return_value=lp),
            patch("maas_code_reviewer.cli.GitClient", return_value=git),
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
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

    def test_missing_gemini_api_key_exits_with_error(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When no Gemini API key is available, the command exits with an error."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        args = _build_parser().parse_args(
            [
                "review-mp",
                "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
            ]
        )

        with pytest.raises(SystemExit) as exc_info:
            handle_review_mp(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "GEMINI_API_KEY" in captured.err


class TestGetGeminiApiKey:
    def _make_args(
        self, gemini_api_key_file: str | None = None
    ) -> argparse.Namespace:
        return argparse.Namespace(gemini_api_key_file=gemini_api_key_file)

    def test_reads_key_from_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("my-secret-key\n")
        assert _get_gemini_api_key(self._make_args(str(key_file))) == "my-secret-key"

    def test_strips_whitespace_from_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("   my-key   \n\n")
        assert _get_gemini_api_key(self._make_args(str(key_file))) == "my-key"

    def test_empty_file_returns_none(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("")
        assert _get_gemini_api_key(self._make_args(str(key_file))) is None

    def test_whitespace_only_file_returns_none(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("   \n  \t\n")
        assert _get_gemini_api_key(self._make_args(str(key_file))) is None

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        assert _get_gemini_api_key(self._make_args(str(missing))) is None

    def test_reads_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        assert _get_gemini_api_key(self._make_args()) == "env-key"

    def test_strips_whitespace_from_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "  env-key\n")
        assert _get_gemini_api_key(self._make_args()) == "env-key"

    def test_empty_env_var_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "")
        assert _get_gemini_api_key(self._make_args()) is None

    def test_whitespace_only_env_var_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "   \n")
        assert _get_gemini_api_key(self._make_args()) is None

    def test_no_file_no_env_var_returns_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        assert _get_gemini_api_key(self._make_args()) is None

    def test_file_takes_precedence_over_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "env-key")
        key_file = tmp_path / "key"
        key_file.write_text("file-key")
        assert _get_gemini_api_key(self._make_args(str(key_file))) == "file-key"


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
                body="[maas-code-reviewer review]\n\nLooks good!",
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
                body="[maas-code-reviewer review]\n\nFake review",
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
                body="[maas-code-reviewer review]\n\nPrevious review.",
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

        expected = (
            f"[maas-code-reviewer review]\n\n{REVIEW_PREAMBLE}\n\nReview body here."
        )
        assert result == expected

    def test_populates_metrics(self, tmp_path: Path) -> None:
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
                    text="OK.", tokens_input=10, tokens_output=20, tokens_thinking=30
                )
            ]
        )

        metrics = ReviewMetrics()
        result = review_merge_proposal(lp, git, llm, mp.url, metrics=metrics)

        assert result is not None
        assert metrics.model_name == "gemini-3-flash-preview"
        assert metrics.tokens_input == 10
        assert metrics.tokens_output == 20
        assert metrics.tokens_thinking == 30
        assert metrics.diff_lines > 0
        assert metrics.diff_size_bytes > 0


class TestBuildParserReviewDiff:
    def test_review_diff_parses_diff_file(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text("--- a/f\n+++ b/f\n")
        parser = _build_parser()
        args = parser.parse_args(["review-diff", "-g", str(key_file), str(diff_file)])
        assert args.command == "review-diff"
        assert args.diff_file == str(diff_file)

    def test_review_diff_parses_stdin_dash(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(["review-diff", "-g", str(key_file), "-"])
        assert args.diff_file == "-"

    def test_review_diff_repo_dir_defaults_to_none(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(["review-diff", "-g", str(key_file), "-"])
        assert args.repo_dir is None

    def test_review_diff_parses_repo_dir(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(
            ["review-diff", "-g", str(key_file), "--repo-dir", str(tmp_path), "-"]
        )
        assert args.repo_dir == str(tmp_path)

    def test_review_diff_model_defaults_to_gemini_3_flash_preview(
        self, tmp_path: Path
    ) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(["review-diff", "-g", str(key_file), "-"])
        assert args.model == "gemini-3-flash-preview"

    def test_review_diff_parses_model(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(
            ["review-diff", "--model", "gemini-2.5-pro", "-g", str(key_file), "-"]
        )
        assert args.model == "gemini-2.5-pro"

    def test_review_diff_gemini_api_key_file_defaults_to_none(self) -> None:
        parser = _build_parser()
        args = parser.parse_args(["review-diff", "patch.diff"])
        assert args.gemini_api_key_file is None

    def test_review_diff_json_output_defaults_to_none(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(["review-diff", "-g", str(key_file), "-"])
        assert args.json_output is None

    def test_review_diff_parses_json_output(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        output_file = tmp_path / "review.json"
        parser = _build_parser()
        args = parser.parse_args(
            [
                "review-diff",
                "-g",
                str(key_file),
                "--json-output",
                str(output_file),
                "-",
            ]
        )
        assert args.json_output == str(output_file)

    def test_review_diff_metrics_defaults_to_none(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(["review-diff", "-g", str(key_file), "-"])
        assert args.metrics is None

    def test_review_diff_parses_metrics(self, tmp_path: Path) -> None:
        key_file = tmp_path / "key"
        key_file.write_text("test-key")
        parser = _build_parser()
        args = parser.parse_args(
            ["review-diff", "-g", str(key_file), "--metrics", "/tmp/m.json", "-"]
        )
        assert args.metrics == "/tmp/m.json"


_STRUCTURED_DIFF = (
    "--- a/src/foo.py\n"
    "+++ b/src/foo.py\n"
    "@@ -1,3 +1,4 @@\n"
    " import os\n"
    "+import sys\n"
    "\n"
    " def main():\n"
)

_VALID_STRUCTURED_RESPONSE = json.dumps(
    {
        "general_comment": "Looks good.",
        "inline_comments": {
            "src/foo.py": {
                "2": "Good addition.",
            }
        },
    }
)

_EMPTY_STRUCTURED_RESPONSE = json.dumps(
    {
        "general_comment": "No issues.",
        "inline_comments": {},
    }
)


class TestHandleReviewDiff:
    def test_reads_diff_from_file_and_prints_review(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """review-diff reads a diff from a file path and prints the LLM review."""
        diff_content = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(diff_content)

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        llm = FakeLLMClient([ScriptedResponse(text="All good.")])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        captured = capsys.readouterr()
        assert "All good." in captured.out
        assert REVIEW_MARKER in captured.out

    def test_reads_diff_from_stdin(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """review-diff reads a diff from stdin when diff_file is '-'."""
        import io

        diff_content = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        monkeypatch.setattr("sys.stdin", io.StringIO(diff_content))

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        llm = FakeLLMClient([ScriptedResponse(text="Stdin review.")])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    "-",
                ]
            )
            handle_review_diff(args)

        captured = capsys.readouterr()
        assert "Stdin review." in captured.out

    def test_uses_custom_repo_dir_for_tools(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The --repo-dir is used as the base for read_file and list_directory tools."""
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        (repo_dir / "AGENTS.md").write_text("# Rules\nBe nice.\n")

        diff_file = tmp_path / "patch.diff"
        diff_file.write_text("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n")

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="Used context.",
                    tool_calls=[ToolCall(name="read_file", args={"path": "AGENTS.md"})],
                )
            ]
        )

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(repo_dir),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        captured = capsys.readouterr()
        assert "Used context." in captured.out

    def test_default_repo_dir_is_cwd(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When --repo-dir is omitted, the current working directory is used."""
        monkeypatch.chdir(tmp_path)

        diff_file = tmp_path / "patch.diff"
        diff_file.write_text("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n")

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        llm = FakeLLMClient([ScriptedResponse(text="CWD review.")])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                ["review-diff", "-g", str(api_key_file), str(diff_file)]
            )
            handle_review_diff(args)

        captured = capsys.readouterr()
        assert "CWD review." in captured.out

    def test_diff_content_passed_to_llm(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The diff text from the file is included in the prompt sent to the LLM."""
        diff_content = (
            "--- a/widget.py\n+++ b/widget.py\n@@ -1 +1 @@\n-broken\n+fixed\n"
        )
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(diff_content)

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        llm = FakeLLMClient([ScriptedResponse(text="Nice fix.")])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        prompt = llm._client.received_prompts[0]
        assert "broken" in prompt
        assert "fixed" in prompt


class TestHandleReviewDiffJsonOutput:
    def test_writes_json_file_when_json_output_given(self, tmp_path: Path) -> None:
        """When --json-output is provided, a JSON file is written instead of stdout."""
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(_STRUCTURED_DIFF)

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        output_file = tmp_path / "review.json"

        llm = FakeLLMClient([ScriptedResponse(text=_VALID_STRUCTURED_RESPONSE)])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    "--json-output",
                    str(output_file),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert "general_comment" in data
        assert "inline_comments" in data

    def test_json_output_contains_general_comment(self, tmp_path: Path) -> None:
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(_STRUCTURED_DIFF)

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        output_file = tmp_path / "review.json"

        llm = FakeLLMClient([ScriptedResponse(text=_VALID_STRUCTURED_RESPONSE)])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    "--json-output",
                    str(output_file),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        data = json.loads(output_file.read_text())
        assert data["general_comment"] == "Looks good."

    def test_json_output_contains_inline_comments(self, tmp_path: Path) -> None:
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(_STRUCTURED_DIFF)

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        output_file = tmp_path / "review.json"

        llm = FakeLLMClient([ScriptedResponse(text=_VALID_STRUCTURED_RESPONSE)])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    "--json-output",
                    str(output_file),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        data = json.loads(output_file.read_text())
        assert data["inline_comments"]["src/foo.py"]["2"] == "Good addition."

    def test_json_output_does_not_print_to_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When --json-output is given, nothing is printed to stdout."""
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(_STRUCTURED_DIFF)

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        output_file = tmp_path / "review.json"

        llm = FakeLLMClient([ScriptedResponse(text=_EMPTY_STRUCTURED_RESPONSE)])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    "--json-output",
                    str(output_file),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        captured = capsys.readouterr()
        assert captured.out == ""

    def test_no_json_output_still_prints_plain_text(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """When --json-output is absent, plain text review is printed to stdout."""
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(_STRUCTURED_DIFF)

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        llm = FakeLLMClient([ScriptedResponse(text="Plain text review.")])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        captured = capsys.readouterr()
        assert "Plain text review." in captured.out
        assert REVIEW_MARKER in captured.out

    def test_json_output_uses_review_diff_structured(self, tmp_path: Path) -> None:
        """When --json-output is set, review_diff_structured is called."""
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(_STRUCTURED_DIFF)

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        output_file = tmp_path / "review.json"

        llm = FakeLLMClient([ScriptedResponse(text=_VALID_STRUCTURED_RESPONSE)])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    "--json-output",
                    str(output_file),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        tool_names = {t.__name__ for t in llm._client.received_tools[0]}
        assert "validate_review" in tool_names

    def test_json_output_from_stdin(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """--json-output works when reading the diff from stdin."""
        import io

        monkeypatch.setattr("sys.stdin", io.StringIO(_STRUCTURED_DIFF))

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        output_file = tmp_path / "review.json"

        llm = FakeLLMClient([ScriptedResponse(text=_EMPTY_STRUCTURED_RESPONSE)])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    "--json-output",
                    str(output_file),
                    "-",
                ]
            )
            handle_review_diff(args)

        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert data["general_comment"] == "No issues."

    def test_missing_gemini_api_key_exits_with_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When no Gemini API key is available, the command exits with an error."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text("--- a/foo\n+++ b/foo\n")

        args = _build_parser().parse_args(["review-diff", str(diff_file)])

        with pytest.raises(SystemExit) as exc_info:
            handle_review_diff(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "GEMINI_API_KEY" in captured.err


class TestMainReviewDiff:
    def test_review_diff_command_delegates_to_handle_review_diff(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        diff_content = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(diff_content)

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        llm = FakeLLMClient([ScriptedResponse(text="Main dispatch works.")])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            main(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    str(diff_file),
                ]
            )

        captured = capsys.readouterr()
        assert "Main dispatch works." in captured.out


_PR_DIFF = (
    "--- a/src/foo.py\n"
    "+++ b/src/foo.py\n"
    "@@ -1,3 +1,4 @@\n"
    " import os\n"
    "+import sys\n"
    "\n"
    " def main():\n"
)

_PR_REVIEW_RESPONSE = json.dumps(
    {
        "general_comment": "Looks good overall.",
        "inline_comments": {
            "src/foo.py": {
                "2": "Nice import.",
            }
        },
    }
)

_PR_EMPTY_RESPONSE = json.dumps(
    {
        "general_comment": "No issues.",
        "inline_comments": {},
    }
)


class TestBuildParserReviewPr:
    def test_review_pr_parses_pr_url(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "https://github.com/owner/repo/pull/42",
            ]
        )
        assert args.pr_url == "https://github.com/owner/repo/pull/42"

    def test_review_pr_requires_pr_url(self) -> None:
        with pytest.raises(SystemExit):
            _build_parser().parse_args(["review-pr", "-g", "key.txt"])

    def test_review_pr_gemini_api_key_file_defaults_to_none(self) -> None:
        args = _build_parser().parse_args(
            ["review-pr", "https://github.com/owner/repo/pull/1"]
        )
        assert args.gemini_api_key_file is None

    def test_review_pr_github_token_defaults_to_none(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "https://github.com/owner/repo/pull/1",
            ]
        )
        assert args.github_token is None

    def test_review_pr_parses_github_token(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "--github-token",
                "ghp_abc123",
                "https://github.com/owner/repo/pull/1",
            ]
        )
        assert args.github_token == "ghp_abc123"

    def test_review_pr_model_defaults_to_gemini_3_flash_preview(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "https://github.com/owner/repo/pull/1",
            ]
        )
        assert args.model == "gemini-3-flash-preview"

    def test_review_pr_parses_model(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "--model",
                "gemini-pro",
                "https://github.com/owner/repo/pull/1",
            ]
        )
        assert args.model == "gemini-pro"

    def test_review_pr_repo_dir_defaults_to_none(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "https://github.com/owner/repo/pull/1",
            ]
        )
        assert args.repo_dir is None

    def test_review_pr_parses_repo_dir(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "--repo-dir",
                "/some/path",
                "https://github.com/owner/repo/pull/1",
            ]
        )
        assert args.repo_dir == "/some/path"

    def test_review_pr_dry_run_defaults_to_false(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "https://github.com/owner/repo/pull/1",
            ]
        )
        assert args.dry_run is False

    def test_review_pr_parses_dry_run(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "--dry-run",
                "https://github.com/owner/repo/pull/1",
            ]
        )
        assert args.dry_run is True

    def test_review_pr_metrics_defaults_to_none(self) -> None:
        args = _build_parser().parse_args(
            ["review-pr", "-g", "key.txt", "https://github.com/owner/repo/pull/1"]
        )
        assert args.metrics is None

    def test_review_pr_parses_metrics(self) -> None:
        args = _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                "key.txt",
                "--metrics",
                "/tmp/m.json",
                "https://github.com/owner/repo/pull/1",
            ]
        )
        assert args.metrics == "/tmp/m.json"


class TestHandleReviewPr:
    def _make_args(
        self,
        tmp_path: Path,
        *,
        pr_url: str = "https://github.com/owner/repo/pull/42",
        github_token: str | None = "ghp_test",
        gemini_api_key_file: str | None = "__default__",
        repo_dir: str | None = None,
        dry_run: bool = False,
    ) -> argparse.Namespace:
        if gemini_api_key_file == "__default__":
            api_key_file = tmp_path / "api_key.txt"
            api_key_file.write_text("fake-key\n")
            gemini_api_key_file = str(api_key_file)
        return _build_parser().parse_args(
            [
                "review-pr",
                *(["-g", gemini_api_key_file] if gemini_api_key_file else []),
                *(["--github-token", github_token] if github_token else []),
                *(["--repo-dir", repo_dir] if repo_dir else []),
                *(["--dry-run"] if dry_run else []),
                pr_url,
            ]
        )

    def test_posts_review_after_fetching_diff(self, tmp_path: Path) -> None:
        """End-to-end: diff is fetched, reviewed, and posted to GitHub."""
        github_client = FakeGitHubClient()
        github_client.add_pull_request(
            "owner", "repo", 42, diff=_PR_DIFF, description="Add sys import"
        )
        llm = FakeLLMClient([ScriptedResponse(text=_PR_REVIEW_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(self._make_args(tmp_path, repo_dir=str(tmp_path)))

        reviews = github_client.get_posted_reviews("owner", "repo", 42)
        assert len(reviews) == 1
        assert reviews[0]["body"] == "Looks good overall."

    def test_inline_comments_are_posted(self, tmp_path: Path) -> None:
        """Inline comments from the LLM response are included in the posted review."""
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_REVIEW_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(self._make_args(tmp_path, repo_dir=str(tmp_path)))

        reviews = github_client.get_posted_reviews("owner", "repo", 42)
        comments = reviews[0]["comments"]
        assert len(comments) == 1
        assert comments[0]["path"] == "src/foo.py"
        assert comments[0]["line"] == 2
        assert comments[0]["body"] == "Nice import."

    def test_no_inline_comments_when_empty(self, tmp_path: Path) -> None:
        """When the LLM returns no inline comments, an empty list is posted."""
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(self._make_args(tmp_path, repo_dir=str(tmp_path)))

        reviews = github_client.get_posted_reviews("owner", "repo", 42)
        assert reviews[0]["comments"] == []

    def test_dry_run_prints_json_to_stdout(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """With --dry-run, the JSON review is printed to stdout, not posted."""
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_REVIEW_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(
                self._make_args(tmp_path, dry_run=True, repo_dir=str(tmp_path))
            )

        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["general_comment"] == "Looks good overall."

    def test_dry_run_does_not_post_review(self, tmp_path: Path) -> None:
        """With --dry-run, no review is posted to GitHub."""
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(
                self._make_args(tmp_path, dry_run=True, repo_dir=str(tmp_path))
            )

        reviews = github_client.get_posted_reviews("owner", "repo", 42)
        assert reviews == []

    def test_token_read_from_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --github-token is absent, GITHUB_TOKEN env var is used."""
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        captured_token: list[str] = []

        def fake_github_client(token: str) -> FakeGitHubClient:
            captured_token.append(token)
            return github_client

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch(
                "maas_code_reviewer.cli.GitHubClient", side_effect=fake_github_client
            ),
        ):
            handle_review_pr(
                self._make_args(tmp_path, github_token=None, repo_dir=str(tmp_path))
            )

        assert captured_token == ["env-token"]

    def test_explicit_token_overrides_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """--github-token takes precedence over GITHUB_TOKEN env var."""
        monkeypatch.setenv("GITHUB_TOKEN", "env-token")
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        captured_token: list[str] = []

        def fake_github_client(token: str) -> FakeGitHubClient:
            captured_token.append(token)
            return github_client

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch(
                "maas_code_reviewer.cli.GitHubClient", side_effect=fake_github_client
            ),
        ):
            handle_review_pr(
                self._make_args(
                    tmp_path, github_token="explicit-token", repo_dir=str(tmp_path)
                )
            )

        assert captured_token == ["explicit-token"]

    def test_missing_token_exits_with_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When no token is available, the command exits with an error message."""
        monkeypatch.delenv("GITHUB_TOKEN", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            handle_review_pr(
                self._make_args(tmp_path, github_token=None, repo_dir=str(tmp_path))
            )

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "GITHUB_TOKEN" in captured.err

    def test_gemini_api_key_read_from_env_var(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --gemini-api-key-file is absent, GEMINI_API_KEY env var is used."""
        monkeypatch.setenv("GEMINI_API_KEY", "env-gemini-key")
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        captured_keys: list[str] = []

        def fake_gemini_client(*, api_key: str, model: str) -> object:
            captured_keys.append(api_key)
            return llm

        with (
            patch(
                "maas_code_reviewer.cli.GeminiClient", side_effect=fake_gemini_client
            ),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(
                self._make_args(
                    tmp_path, gemini_api_key_file=None, repo_dir=str(tmp_path)
                )
            )

        assert captured_keys == ["env-gemini-key"]

    def test_missing_gemini_api_key_exits_with_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """When no Gemini API key is available, the command exits with an error."""
        monkeypatch.delenv("GEMINI_API_KEY", raising=False)

        with pytest.raises(SystemExit) as exc_info:
            handle_review_pr(
                self._make_args(
                    tmp_path, gemini_api_key_file=None, repo_dir=str(tmp_path)
                )
            )

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "GEMINI_API_KEY" in captured.err

    def test_description_passed_to_llm(self, tmp_path: Path) -> None:
        """The PR description is included in the LLM prompt."""
        github_client = FakeGitHubClient()
        github_client.add_pull_request(
            "owner", "repo", 42, diff=_PR_DIFF, description="Fix the widget bug"
        )
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(self._make_args(tmp_path, repo_dir=str(tmp_path)))

        prompt = llm._client.received_prompts[0]
        assert "Fix the widget bug" in prompt

    def test_diff_passed_to_llm(self, tmp_path: Path) -> None:
        """The PR diff is included in the LLM prompt."""
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(self._make_args(tmp_path, repo_dir=str(tmp_path)))

        prompt = llm._client.received_prompts[0]
        assert "import sys" in prompt

    def test_default_repo_dir_is_cwd(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When --repo-dir is omitted, the current working directory is used."""
        monkeypatch.chdir(tmp_path)
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            # No --repo-dir; args.repo_dir will be None
            handle_review_pr(self._make_args(tmp_path, repo_dir=None))

        # If we get here without error, cwd was used successfully.
        reviews = github_client.get_posted_reviews("owner", "repo", 42)
        assert len(reviews) == 1

    def test_validate_review_tool_provided_to_llm(self, tmp_path: Path) -> None:
        """The validate_review tool is provided to the LLM for structured output."""
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(self._make_args(tmp_path, repo_dir=str(tmp_path)))

        tool_names = {t.__name__ for t in llm._client.received_tools[0]}
        assert "validate_review" in tool_names


class TestHandleReviewDiffMetrics:
    def test_no_metrics_flag_no_file_written(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text("--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n")
        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        llm = FakeLLMClient([ScriptedResponse(text="All good.")])

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        # No metrics file should exist
        assert not (tmp_path / "metrics.json").exists()

    def test_metrics_written_with_correct_fields(self, tmp_path: Path) -> None:
        diff_content = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@\n-old\n+new\n"
        diff_file = tmp_path / "patch.diff"
        diff_file.write_text(diff_content)
        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")
        metrics_file = tmp_path / "metrics.json"

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="All good.",
                    tokens_input=10,
                    tokens_output=20,
                    tokens_thinking=30,
                )
            ]
        )

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(tmp_path),
                    "--metrics",
                    str(metrics_file),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        assert metrics_file.exists()
        data = json.loads(metrics_file.read_text())
        assert data["model_name"] == "gemini-3-flash-preview"
        assert data["tokens_input"] == 10
        assert data["tokens_output"] == 20
        assert data["tokens_thinking"] == 30
        assert data["diff_lines"] == diff_content.count("\n")
        assert data["diff_size_bytes"] == len(diff_content.encode("utf-8"))
        assert data["files_read"] == 0
        assert data["agents_md_read"] is False

    def test_metrics_tracks_files_read(self, tmp_path: Path) -> None:
        repo_dir = tmp_path / "myrepo"
        repo_dir.mkdir()
        (repo_dir / "AGENTS.md").write_text("# Rules\nBe nice.\n")

        diff_file = tmp_path / "patch.diff"
        diff_file.write_text("--- a/x.py\n+++ b/x.py\n@@ -1 +1 @@\n-a\n+b\n")
        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")
        metrics_file = tmp_path / "metrics.json"

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="Used context.",
                    tool_calls=[ToolCall(name="read_file", args={"path": "AGENTS.md"})],
                )
            ]
        )

        with patch("maas_code_reviewer.cli.GeminiClient", return_value=llm):
            args = _build_parser().parse_args(
                [
                    "review-diff",
                    "-g",
                    str(api_key_file),
                    "--repo-dir",
                    str(repo_dir),
                    "--metrics",
                    str(metrics_file),
                    str(diff_file),
                ]
            )
            handle_review_diff(args)

        data = json.loads(metrics_file.read_text())
        assert data["files_read"] == 1
        assert data["agents_md_read"] is True


class TestHandleReviewPrMetrics:
    def _make_args(
        self,
        tmp_path: Path,
        *,
        pr_url: str = "https://github.com/owner/repo/pull/42",
        github_token: str | None = "ghp_test",
        repo_dir: str | None = None,
        dry_run: bool = False,
        metrics: str | None = None,
    ) -> argparse.Namespace:
        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")
        return _build_parser().parse_args(
            [
                "review-pr",
                "-g",
                str(api_key_file),
                *(["--github-token", github_token] if github_token else []),
                *(["--repo-dir", repo_dir] if repo_dir else []),
                *(["--dry-run"] if dry_run else []),
                *(["--metrics", metrics] if metrics else []),
                pr_url,
            ]
        )

    def test_no_metrics_flag_no_file_written(self, tmp_path: Path) -> None:
        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(self._make_args(tmp_path, repo_dir=str(tmp_path)))

        assert not (tmp_path / "metrics.json").exists()

    def test_metrics_written_with_correct_fields(self, tmp_path: Path) -> None:
        github_client = FakeGitHubClient()
        github_client.add_pull_request(
            "owner", "repo", 42, diff=_PR_DIFF, description="Add sys import"
        )
        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text=_PR_REVIEW_RESPONSE,
                    tokens_input=100,
                    tokens_output=200,
                    tokens_thinking=300,
                )
            ]
        )
        metrics_file = tmp_path / "metrics.json"

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            handle_review_pr(
                self._make_args(
                    tmp_path, repo_dir=str(tmp_path), metrics=str(metrics_file)
                )
            )

        assert metrics_file.exists()
        data = json.loads(metrics_file.read_text())
        assert data["model_name"] == "gemini-3-flash-preview"
        assert data["tokens_input"] == 100
        assert data["tokens_output"] == 200
        assert data["tokens_thinking"] == 300
        assert data["diff_lines"] == _PR_DIFF.count("\n")
        assert data["diff_size_bytes"] == len(_PR_DIFF.encode("utf-8"))
        assert data["files_read"] == 0
        assert data["agents_md_read"] is False


class TestHandleReviewMpMetrics:
    def test_metrics_written_with_correct_fields(self, tmp_path: Path) -> None:
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
                    text="Looks great.",
                    tokens_input=50,
                    tokens_output=60,
                    tokens_thinking=70,
                )
            ]
        )

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")
        metrics_file = tmp_path / "metrics.json"

        with (
            patch("maas_code_reviewer.cli.LaunchpadClient", return_value=lp),
            patch("maas_code_reviewer.cli.GitClient", return_value=git),
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
        ):
            args = _build_parser().parse_args(
                [
                    "review-mp",
                    "--dry-run",
                    "--gemini-api-key-file",
                    str(api_key_file),
                    "--metrics",
                    str(metrics_file),
                    mp.url,
                ]
            )
            handle_review_mp(args)

        assert metrics_file.exists()
        data = json.loads(metrics_file.read_text())
        assert data["model_name"] == "gemini-3-flash-preview"
        assert data["tokens_input"] == 50
        assert data["tokens_output"] == 60
        assert data["tokens_thinking"] == 70
        assert data["diff_lines"] > 0
        assert data["diff_size_bytes"] > 0

    def test_no_metrics_flag_no_file_written(self, tmp_path: Path) -> None:
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
            patch("maas_code_reviewer.cli.LaunchpadClient", return_value=lp),
            patch("maas_code_reviewer.cli.GitClient", return_value=git),
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
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

        assert not (tmp_path / "metrics.json").exists()

    def test_no_metrics_written_when_already_reviewed(self, tmp_path: Path) -> None:
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
                body="[maas-code-reviewer review]\n\nAlready done.",
                date=datetime(2025, 6, 15, 10, 0, 0, tzinfo=UTC),
            ),
        )

        llm = FakeLLMClient()

        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")
        metrics_file = tmp_path / "metrics.json"

        with (
            patch("maas_code_reviewer.cli.LaunchpadClient", return_value=lp),
            patch("maas_code_reviewer.cli.GitClient", return_value=git),
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
        ):
            args = _build_parser().parse_args(
                [
                    "review-mp",
                    "--gemini-api-key-file",
                    str(api_key_file),
                    "--metrics",
                    str(metrics_file),
                    mp.url,
                ]
            )
            handle_review_mp(args)

        assert not metrics_file.exists()


class TestMainReviewPr:
    def test_review_pr_command_delegates_to_handle_review_pr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api_key_file = tmp_path / "api_key.txt"
        api_key_file.write_text("fake-key\n")

        github_client = FakeGitHubClient()
        github_client.add_pull_request("owner", "repo", 42, diff=_PR_DIFF)
        llm = FakeLLMClient([ScriptedResponse(text=_PR_EMPTY_RESPONSE)])

        with (
            patch("maas_code_reviewer.cli.GeminiClient", return_value=llm),
            patch("maas_code_reviewer.cli.GitHubClient", return_value=github_client),
        ):
            main(
                [
                    "review-pr",
                    "-g",
                    str(api_key_file),
                    "--github-token",
                    "ghp_test",
                    "--repo-dir",
                    str(tmp_path),
                    "https://github.com/owner/repo/pull/42",
                ]
            )

        reviews = github_client.get_posted_reviews("owner", "repo", 42)
        assert len(reviews) == 1
