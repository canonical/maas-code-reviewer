# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import pytest
from google.genai import types

from maas_code_reviewer.llm_client import _RESUME_PROMPT, _print_thoughts
from tests.fake_llm import FakeLLMClient, ScriptedResponse, ToolCall


class TestPrintThoughts:
    # candidates is None when the *prompt* is blocked by a safety filter before
    # any generation starts.  The API sets prompt_feedback.block_reason instead
    # and returns no candidates at all.
    def test_none_candidates_prints_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        response = types.GenerateContentResponse(candidates=None)
        _print_thoughts(response)
        assert capsys.readouterr().err == ""

    # candidates=[] is the same safety-block scenario reached via a slightly
    # different API path; both are falsy, so the same guard covers them.
    def test_empty_candidates_prints_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        response = types.GenerateContentResponse(candidates=[])
        _print_thoughts(response)
        assert capsys.readouterr().err == ""

    # content is None when a candidate was started but its *output* was then
    # safety-filtered during generation (finish_reason=SAFETY).  The candidate
    # object exists but carries no returnable content.
    def test_none_content_prints_nothing(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        response = types.GenerateContentResponse(
            candidates=[types.Candidate(content=None)]
        )
        _print_thoughts(response)
        assert capsys.readouterr().err == ""

    def test_thought_part_is_printed_to_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        response = types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        parts=[
                            types.Part(text="I am reasoning.", thought=True),
                            types.Part(text="The code looks good."),
                        ],
                        role="model",
                    )
                )
            ]
        )
        _print_thoughts(response)
        assert capsys.readouterr().err == "[Thinking]\nI am reasoning.\n\n"

    def test_afc_history_thoughts_are_printed_to_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # model turn: thought produced while deciding to call a tool
        model_content = types.Content(
            parts=[types.Part(text="I need to call read_file.", thought=True)],
            role="model",
        )
        # user turn: function response — should be skipped
        user_content = types.Content(
            parts=[types.Part(text="file contents here")],
            role="user",
        )
        response = types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        parts=[types.Part(text="Review complete.")],
                        role="model",
                    )
                )
            ],
            automatic_function_calling_history=[model_content, user_content],
        )
        _print_thoughts(response)
        captured = capsys.readouterr()
        # AFC-step thought is printed; user-role entry and non-thought final
        # part are both skipped.
        assert captured.err == "[Thinking (tool step)]\nI need to call read_file.\n\n"


class TestTokenCountProperties:
    def test_token_counts_reflect_scripted_values(self) -> None:
        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text="ok", tokens_input=100, tokens_output=50, tokens_thinking=200
                )
            ]
        )
        llm.review("prompt", [])
        assert llm.last_tokens_input == 100
        assert llm.last_tokens_output == 50
        assert llm.last_tokens_thinking == 200

    def test_token_counts_default_to_zero(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        llm.review("prompt", [])
        assert llm.last_tokens_input == 0
        assert llm.last_tokens_output == 0
        assert llm.last_tokens_thinking == 0

    def test_token_counts_reset_when_usage_metadata_is_none(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok", no_usage_metadata=True)])
        llm.review("prompt", [])
        assert llm.last_tokens_input == 0
        assert llm.last_tokens_output == 0
        assert llm.last_tokens_thinking == 0

    def test_model_property_returns_model_name(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        assert llm.model == "gemini-3-flash-preview"


class TestGoogleSearch:
    def test_google_search_tool_always_present(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        llm.review("prompt", [])
        raw_tools = llm._client.received_raw_tools[0]
        assert any(
            getattr(tool, "google_search", None) is not None for tool in raw_tools
        )


class TestResumeOnNoText:
    # The model can end its turn on a function_call with no text — e.g. when
    # automatic function calling hits its remote-call cap. response.text is
    # then None, and review() must resume the chat to elicit a text answer.
    def test_resumes_when_first_response_is_function_call_only(self) -> None:
        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text=None,
                    pending_function_call=ToolCall(
                        name="list_directory", args={"path": "src"}
                    ),
                ),
                ScriptedResponse(text="final answer"),
            ]
        )
        result = llm.review("prompt", [])
        assert result == "final answer"
        assert llm._client.received_prompts == ["prompt", _RESUME_PROMPT]

    def test_does_not_resume_when_text_returned(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        llm.review("prompt", [])
        assert llm._client.received_prompts == ["prompt"]

    def test_accumulates_usage_across_resume_turns(self) -> None:
        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text=None,
                    pending_function_call=ToolCall(
                        name="list_directory", args={"path": "src"}
                    ),
                    tokens_input=100,
                    tokens_output=10,
                    tokens_thinking=20,
                ),
                ScriptedResponse(
                    text="ok",
                    tokens_input=5,
                    tokens_output=15,
                    tokens_thinking=25,
                ),
            ]
        )
        llm.review("prompt", [])
        assert llm.last_tokens_input == 105
        assert llm.last_tokens_output == 25
        assert llm.last_tokens_thinking == 45

    def test_gives_up_after_max_resume_attempts(self) -> None:
        # Four function-call-only responses: one initial turn plus three
        # resume attempts. After that review() returns an empty string.
        no_text = ScriptedResponse(
            text=None,
            pending_function_call=ToolCall(
                name="list_directory", args={"path": "src"}
            ),
        )
        llm = FakeLLMClient([no_text, no_text, no_text, no_text])
        result = llm.review("prompt", [])
        assert result == ""
        assert len(llm._client.received_prompts) == 4



class TestToolCallTracking:
    def test_default_limit_is_recorded(self) -> None:
        from maas_code_reviewer.llm_client import DEFAULT_MAX_TOOL_CALLS

        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        llm.review("prompt", [])
        assert llm.last_tool_call_limit == DEFAULT_MAX_TOOL_CALLS
        assert llm.last_tool_call_limit_reached is False
        assert llm.last_resume_attempts == 0

    def test_custom_limit_is_recorded(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        llm.review("prompt", [], max_tool_calls=15)
        assert llm.last_tool_call_limit == 15

    def test_limit_reached_flag_set_on_resume(self) -> None:
        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text=None,
                    pending_function_call=ToolCall(
                        name="list_directory", args={"path": "src"}
                    ),
                ),
                ScriptedResponse(text="ok"),
            ]
        )
        llm.review("prompt", [])
        assert llm.last_tool_call_limit_reached is True
        assert llm.last_resume_attempts == 1

    def test_limit_reached_flag_false_when_text_returned(self) -> None:
        llm = FakeLLMClient([ScriptedResponse(text="ok")])
        llm.review("prompt", [])
        assert llm.last_tool_call_limit_reached is False
        assert llm.last_resume_attempts == 0

    def test_logs_limit_reached_to_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        llm = FakeLLMClient(
            [
                ScriptedResponse(
                    text=None,
                    pending_function_call=ToolCall(
                        name="list_directory", args={"path": "src"}
                    ),
                ),
                ScriptedResponse(text="ok"),
            ]
        )
        llm.review("prompt", [], max_tool_calls=5)
        err = capsys.readouterr().err
        assert "Tool-call limit (5) reached" in err
        assert "resume_attempts: 1" in err
