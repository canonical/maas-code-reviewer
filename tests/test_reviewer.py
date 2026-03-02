from __future__ import annotations

from lp_ci_tools.reviewer import (
    REVIEW_MARKER,
    SYSTEM_INSTRUCTION,
    TRUNCATION_NOTE,
    _build_prompt,
    _truncate_diff,
    review_diff,
)
from tests.fake_llm import FakeLLMClient, ScriptedResponse, ToolCall


def _make_read_file(files: dict[str, str] | None = None) -> callable:
    """Return a ``read_file`` callable backed by *files*."""
    store = files or {}

    def read_file(path: str) -> str:
        if path in store:
            return store[path]
        return f"Error: file not found: {path}"

    return read_file


def _make_list_directory(dirs: dict[str, list[str]] | None = None) -> callable:
    """Return a ``list_directory`` callable backed by *dirs*."""
    store = dirs or {}

    def list_directory(path: str) -> str:
        if path in store:
            return "\n".join(store[path])
        return f"Error: directory not found: {path}"

    return list_directory


class TestBuildPrompt:
    def test_contains_system_instruction(self) -> None:
        prompt = _build_prompt("some diff", None)
        assert SYSTEM_INSTRUCTION in prompt

    def test_contains_diff(self) -> None:
        prompt = _build_prompt("my-diff-content", None)
        assert "my-diff-content" in prompt

    def test_diff_wrapped_in_code_block(self) -> None:
        prompt = _build_prompt("some diff", None)
        assert "```\nsome diff\n```" in prompt

    def test_includes_description_when_provided(self) -> None:
        prompt = _build_prompt("diff", "Fix the widget")
        assert "Fix the widget" in prompt
        assert "## Merge Proposal Description" in prompt

    def test_no_description_section_when_none(self) -> None:
        prompt = _build_prompt("diff", None)
        assert "## Merge Proposal Description" not in prompt

    def test_includes_instructions_section(self) -> None:
        prompt = _build_prompt("diff", None)
        assert "## Instructions" in prompt
        assert "provided tools" in prompt


class TestTruncateDiff:
    def test_short_diff_unchanged(self) -> None:
        diff = "short diff"
        assert _truncate_diff(diff, 100) == diff

    def test_exact_limit_unchanged(self) -> None:
        diff = "a" * 50
        assert _truncate_diff(diff, 50) == diff

    def test_over_limit_truncated(self) -> None:
        diff = "a" * 100
        result = _truncate_diff(diff, 50)
        assert result.startswith("a" * 50)
        assert TRUNCATION_NOTE in result
        assert len(result) == 50 + len(TRUNCATION_NOTE)

    def test_truncation_note_appended(self) -> None:
        diff = "x" * 200
        result = _truncate_diff(diff, 10)
        assert result.endswith(TRUNCATION_NOTE)


class TestReviewDiff:
    def test_returns_review_with_marker_prefix(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="Looks good!")])
        result = review_diff(
            llm,
            diff="some diff",
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert result.startswith(REVIEW_MARKER)
        assert "Looks good!" in result

    def test_marker_is_on_first_line(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="All fine.")])
        result = review_diff(
            llm,
            diff="d",
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        first_line = result.split("\n")[0]
        assert first_line == REVIEW_MARKER

    def test_review_text_separated_from_marker_by_blank_line(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="Review body.")])
        result = review_diff(
            llm,
            diff="d",
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert f"{REVIEW_MARKER}\n\nReview body." == result

    def test_prompt_contains_diff(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        review_diff(
            llm,
            diff="my-diff",
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert "my-diff" in llm.received_prompts[0]

    def test_prompt_contains_description(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        review_diff(
            llm,
            diff="d",
            description="Add feature X",
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert "Add feature X" in llm.received_prompts[0]

    def test_diff_truncated_when_exceeding_max(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        big_diff = "x" * 200
        review_diff(
            llm,
            diff=big_diff,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
            max_diff_chars=50,
        )
        prompt = llm.received_prompts[0]
        # The full 200-char diff should NOT appear
        assert "x" * 200 not in prompt
        # But the truncated portion and note should
        assert "x" * 50 in prompt
        assert TRUNCATION_NOTE in prompt

    def test_diff_not_truncated_when_under_max(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        diff = "y" * 50
        review_diff(
            llm,
            diff=diff,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
            max_diff_chars=100,
        )
        prompt = llm.received_prompts[0]
        assert "y" * 50 in prompt
        assert TRUNCATION_NOTE not in prompt

    def test_tools_provided_to_llm(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        rf = _make_read_file()
        ld = _make_list_directory()
        review_diff(
            llm,
            diff="d",
            description=None,
            read_file=rf,
            list_directory=ld,
        )
        tool_names = {t.__name__ for t in llm.received_tools[0]}
        assert "read_file" in tool_names
        assert "list_directory" in tool_names

    def test_tool_invocation_read_file(self) -> None:
        """The fake LLM invokes read_file and we verify it worked."""
        rf = _make_read_file({"AGENTS.md": "# Agent rules\nBe nice."})
        ld = _make_list_directory()

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="Reviewed with context from AGENTS.md.",
                    tool_calls=[ToolCall(name="read_file", args={"path": "AGENTS.md"})],
                ),
            ]
        )
        result = review_diff(
            llm,
            diff="diff content",
            description=None,
            read_file=rf,
            list_directory=ld,
        )
        assert "Reviewed with context from AGENTS.md." in result

    def test_tool_invocation_list_directory(self) -> None:
        rf = _make_read_file()
        ld = _make_list_directory({"src": ["main.py", "utils.py"]})

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="Looks fine.",
                    tool_calls=[ToolCall(name="list_directory", args={"path": "src"})],
                ),
            ]
        )
        result = review_diff(
            llm,
            diff="d",
            description=None,
            read_file=rf,
            list_directory=ld,
        )
        assert "Looks fine." in result

    def test_multiple_tool_calls(self) -> None:
        rf = _make_read_file(
            {"README.md": "readme content", "setup.py": "setup content"}
        )
        ld = _make_list_directory({".": ["README.md", "setup.py"]})

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="All good after checking files.",
                    tool_calls=[
                        ToolCall(name="list_directory", args={"path": "."}),
                        ToolCall(name="read_file", args={"path": "README.md"}),
                        ToolCall(name="read_file", args={"path": "setup.py"}),
                    ],
                ),
            ]
        )
        result = review_diff(
            llm,
            diff="d",
            description=None,
            read_file=rf,
            list_directory=ld,
        )
        assert "All good after checking files." in result

    def test_no_description_still_works(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="fine")])
        result = review_diff(
            llm,
            diff="d",
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert "fine" in result

    def test_tool_read_file_returns_error_for_missing_file(self) -> None:
        rf = _make_read_file({"existing.py": "content"})
        ld = _make_list_directory()

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="File was missing.",
                    tool_calls=[
                        ToolCall(name="read_file", args={"path": "missing.py"}),
                    ],
                ),
            ]
        )
        result = review_diff(
            llm,
            diff="d",
            description=None,
            read_file=rf,
            list_directory=ld,
        )
        assert "File was missing." in result

    def test_tool_list_directory_returns_error_for_missing_dir(self) -> None:
        rf = _make_read_file()
        ld = _make_list_directory({"src": ["main.py"]})

        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="Dir was missing.",
                    tool_calls=[
                        ToolCall(name="list_directory", args={"path": "nope"}),
                    ],
                ),
            ]
        )
        result = review_diff(
            llm,
            diff="d",
            description=None,
            read_file=rf,
            list_directory=ld,
        )
        assert "Dir was missing." in result

    def test_empty_diff(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="No changes.")])
        result = review_diff(
            llm,
            diff="",
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert "No changes." in result
        assert result.startswith(REVIEW_MARKER)
