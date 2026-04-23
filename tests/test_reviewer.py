# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import json

import pytest

from maas_code_reviewer.reviewer import (
    REVIEW_MARKER,
    REVIEW_PREAMBLE,
    STRUCTURED_SYSTEM_INSTRUCTION,
    SYSTEM_INSTRUCTION,
    TRUNCATION_NOTE,
    _build_prompt,
    _build_structured_prompt,
    _extract_json,
    _truncate_diff,
    _validate_review,
    review_diff,
    review_diff_structured,
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
        assert f"{REVIEW_MARKER}\n\n{REVIEW_PREAMBLE}\n\nReview body." == result

    def test_prompt_contains_diff(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        review_diff(
            llm,
            diff="my-diff",
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert "my-diff" in llm._client.received_prompts[0]

    def test_prompt_contains_description(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        review_diff(
            llm,
            diff="d",
            description="Add feature X",
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert "Add feature X" in llm._client.received_prompts[0]

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
        prompt = llm._client.received_prompts[0]
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
        prompt = llm._client.received_prompts[0]
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
        tool_names = {t.__name__ for t in llm._client.received_tools[0]}
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


# ---------------------------------------------------------------------------
# Helpers shared by structured-review tests
# ---------------------------------------------------------------------------

_SIMPLE_DIFF = """\
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,3 +1,4 @@
 import os
+import sys

 def main():
"""

_VALID_JSON_RESPONSE = json.dumps(
    {
        "general_comment": "Looks good.",
        "inline_comments": {
            "src/foo.py": {
                "2": "Good addition.",
            }
        },
    }
)

_EMPTY_INLINE_JSON_RESPONSE = json.dumps(
    {
        "general_comment": "No issues.",
        "inline_comments": {},
    }
)


class TestValidateReview:
    def test_returns_empty_string_for_valid_json(self) -> None:
        result = _validate_review(_VALID_JSON_RESPONSE, _SIMPLE_DIFF)
        assert result == ""

    def test_returns_error_for_invalid_json(self) -> None:
        result = _validate_review("not json {{{", _SIMPLE_DIFF)
        assert result.startswith("Invalid JSON:")

    def test_returns_errors_for_invalid_review(self) -> None:
        bad_review = json.dumps({"wrong_key": "value", "inline_comments": {}})
        result = _validate_review(bad_review, _SIMPLE_DIFF)
        assert "general_comment" in result

    def test_returns_multiple_errors_joined_by_newline(self) -> None:
        bad_review = json.dumps({"inline_comments": {}})
        result = _validate_review(bad_review, _SIMPLE_DIFF)
        assert "\n" not in result  # only one error: missing general_comment

    def test_validates_against_diff(self) -> None:
        review = json.dumps(
            {
                "general_comment": "ok",
                "inline_comments": {"nonexistent.py": {"1": "nope"}},
            }
        )
        result = _validate_review(review, _SIMPLE_DIFF)
        assert "nonexistent.py" in result


class TestExtractJson:
    def test_plain_json_unchanged(self) -> None:
        text = '{"a": 1}'
        assert _extract_json(text) == '{"a": 1}'

    def test_strips_json_fence(self) -> None:
        text = '```json\n{"a": 1}\n```'
        assert _extract_json(text) == '{"a": 1}'

    def test_strips_plain_fence(self) -> None:
        text = '```\n{"a": 1}\n```'
        assert _extract_json(text) == '{"a": 1}'

    def test_strips_surrounding_whitespace(self) -> None:
        text = '  \n  {"a": 1}  \n  '
        assert _extract_json(text) == '{"a": 1}'

    def test_strips_fence_and_whitespace(self) -> None:
        text = '  ```json\n  {"a": 1}\n  ```  '
        result = _extract_json(text)
        assert result == '{"a": 1}'


class TestBuildStructuredPrompt:
    def test_contains_structured_system_instruction(self) -> None:
        prompt = _build_structured_prompt("some diff", None)
        assert STRUCTURED_SYSTEM_INSTRUCTION in prompt

    def test_contains_diff(self) -> None:
        prompt = _build_structured_prompt("my-diff-content", None)
        assert "my-diff-content" in prompt

    def test_diff_wrapped_in_code_block(self) -> None:
        prompt = _build_structured_prompt("some diff", None)
        assert "```\nsome diff\n```" in prompt

    def test_includes_description_when_provided(self) -> None:
        prompt = _build_structured_prompt("diff", "Fix the widget")
        assert "Fix the widget" in prompt

    def test_no_description_section_when_none(self) -> None:
        prompt = _build_structured_prompt("diff", None)
        assert "Fix the widget" not in prompt

    def test_mentions_validate_review_tool(self) -> None:
        prompt = _build_structured_prompt("diff", None)
        assert "validate_review" in prompt

    def test_includes_instructions_section(self) -> None:
        prompt = _build_structured_prompt("diff", None)
        assert "## Instructions" in prompt


class TestReviewDiffStructured:
    def test_returns_dict(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text=_VALID_JSON_RESPONSE)])
        result = review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert isinstance(result, dict)

    def test_returns_general_comment(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text=_VALID_JSON_RESPONSE)])
        result = review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert result["general_comment"] == "Looks good."

    def test_returns_inline_comments(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text=_VALID_JSON_RESPONSE)])
        result = review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert result["inline_comments"]["src/foo.py"]["2"] == "Good addition."

    def test_empty_inline_comments_allowed(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text=_EMPTY_INLINE_JSON_RESPONSE)])
        result = review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert result["inline_comments"] == {}

    def test_read_file_tool_provided_to_llm(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text=_VALID_JSON_RESPONSE)])
        review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        tool_names = {t.__name__ for t in llm._client.received_tools[0]}
        assert "read_file" in tool_names

    def test_prompt_contains_diff(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text=_EMPTY_INLINE_JSON_RESPONSE)])
        review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert _SIMPLE_DIFF in llm._client.received_prompts[0]

    def test_prompt_contains_description(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text=_EMPTY_INLINE_JSON_RESPONSE)])
        review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description="Add sys import",
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert "Add sys import" in llm._client.received_prompts[0]

    def test_strips_json_fence_from_response(self) -> None:
        fenced = f"```json\n{_VALID_JSON_RESPONSE}\n```"
        llm = FakeLLMClient([ScriptedResponse(text=fenced)])
        result = review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert result["general_comment"] == "Looks good."

    def test_diff_truncated_when_exceeding_max(self) -> None:
        big_diff = "x" * 200
        # The LLM receives a truncated diff — we just need it to return valid JSON.
        llm = FakeLLMClient([ScriptedResponse(text=_EMPTY_INLINE_JSON_RESPONSE)])
        result = review_diff_structured(
            llm,
            diff=big_diff,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
            max_diff_chars=50,
        )
        # The truncated diff has no real files, so empty inline_comments is valid.
        assert isinstance(result, dict)
        prompt = llm._client.received_prompts[0]
        assert "x" * 200 not in prompt
        assert TRUNCATION_NOTE in prompt

    def test_validate_review_tool_called_by_llm(self) -> None:
        """When the scripted LLM calls validate_review, no exception is raised."""
        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text=_VALID_JSON_RESPONSE,
                    tool_calls=[
                        ToolCall(
                            name="validate_review",
                            args={"json_text": _VALID_JSON_RESPONSE},
                        )
                    ],
                )
            ]
        )
        # Should not raise
        result = review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        assert result["general_comment"] == "Looks good."

    def test_raises_on_invalid_json_response(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="this is not json at all")])
        with pytest.raises(Exception):
            review_diff_structured(
                llm,
                diff=_SIMPLE_DIFF,
                description=None,
                read_file=_make_read_file(),
                list_directory=_make_list_directory(),
            )

    def test_validate_review_tool_provided_to_llm(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text=_VALID_JSON_RESPONSE)])
        review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=_make_read_file(),
            list_directory=_make_list_directory(),
        )
        tool_names = {t.__name__ for t in llm._client.received_tools[0]}
        assert "validate_review" in tool_names

    def test_tool_calls_read_file_for_context(self) -> None:
        rf = _make_read_file({"src/foo.py": "import os\nimport sys\n"})
        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text=_VALID_JSON_RESPONSE,
                    tool_calls=[
                        ToolCall(name="read_file", args={"path": "src/foo.py"})
                    ],
                )
            ]
        )
        result = review_diff_structured(
            llm,
            diff=_SIMPLE_DIFF,
            description=None,
            read_file=rf,
            list_directory=_make_list_directory(),
        )
        assert result["general_comment"] == "Looks good."
