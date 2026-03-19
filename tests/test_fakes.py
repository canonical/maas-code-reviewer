"""Tests for edge cases in fake objects and factories."""

from __future__ import annotations

from pathlib import Path

import pytest

from maas_code_reviewer.launchpad_client import web_url_to_api_url
from tests.fake_git import FakeGitClient
from tests.fake_launchpadlib import (
    FakeLaunchpad,
    make_fake_comment,
    make_fake_mp,
)
from tests.fake_llm import FakeLLMClient, ScriptedResponse, ToolCall


class TestFakeLLMClientErrors:
    def test_raises_when_no_scripted_responses(self) -> None:
        llm = FakeLLMClient()

        with pytest.raises(
            RuntimeError, match="FakeGenaiClient: no more scripted responses"
        ):
            llm.review("prompt", [])

    def test_raises_when_tool_not_found(self) -> None:
        def some_tool(path: str) -> str:
            return path  # pragma: no cover

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="ok",
                    tool_calls=[ToolCall(name="nonexistent", args={"x": "y"})],
                ),
            ]
        )

        with pytest.raises(
            RuntimeError, match="FakeGenaiClient: tool 'nonexistent' not found"
        ):
            llm.review("prompt", [some_tool])


class TestFakeLaunchpadlibEdgeCases:
    def test_add_comment_raises_for_unknown_mp(self) -> None:
        fake_lp = FakeLaunchpad()
        comment = make_fake_comment(author="alice", body="hi")

        with pytest.raises(KeyError, match="No merge proposal"):
            fake_lp.add_comment("https://code.launchpad.net/unknown", comment)

    def test_load_raises_for_unknown_url(self) -> None:
        fake_lp = FakeLaunchpad()

        with pytest.raises(KeyError, match="Nothing loaded"):
            fake_lp.load("https://example.com/unknown")

    def test_web_url_to_api_url_returns_unchanged_for_non_lp_url(self) -> None:
        url = "https://example.com/something"
        assert web_url_to_api_url(url) == url

    def test_create_comment_on_fake_mp(self) -> None:
        fake_lp = FakeLaunchpad(bot_username="ci-bot")
        mp = make_fake_mp()
        fake_lp.add_merge_proposal("myproject", mp)

        mp.createComment(subject="Review", content="Looks good!")

        assert len(mp.all_comments) == 1
        assert mp.all_comments[0].message_body == "Looks good!"
        assert mp.all_comments[0].author.name == "ci-bot"


class TestFakeGitClientBareRepo:
    def test_create_bare_repo(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        bare_repo = tmp_path / "bare.git"
        client.create_repo(bare_repo, bare=True)

        assert bare_repo.exists()
        # A bare repo has a HEAD file at the top level
        assert (bare_repo / "HEAD").exists()
