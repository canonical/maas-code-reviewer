# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import sys
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import types

# Automatic function calling (AFC) executes client-side tool calls and feeds
# the results back to the model for us. The SDK default caps this at 10
# remote calls, which a review that reads several files can exceed — and when
# the cap is hit the model's final turn is left as an unexecuted tool call
# with no text. This is the default cap used when no explicit value is given
# via the CLI's --max-tool-calls option.
DEFAULT_MAX_TOOL_CALLS = 30

# When the model nonetheless ends a turn with no text (see above, or a turn
# that is only thoughts), resume the chat this many times to elicit a text
# answer before giving up.
_MAX_RESUME_ATTEMPTS = 3

_RESUME_PROMPT = (
    "You have not produced a text answer yet. Stop calling tools and output "
    "your final review now."
)


class GeminiClient:
    """LLM client backed by the Google Gemini API.

    Uses ``chats.create`` with automatic function calling — the SDK
    handles tool dispatch, result feeding, and multi-turn looping
    internally.
    """

    def __init__(
        self,
        *,
        api_key: str = "",
        model: str = "gemini-3-flash-preview",
        client: genai.Client | None = None,
    ) -> None:
        if client is not None:
            self._client = client
        else:
            self._client = genai.Client(api_key=api_key)  # pragma: no cover
        self._model = model
        self._last_tokens_input: int = 0
        self._last_tokens_output: int = 0
        self._last_tokens_thinking: int = 0
        self._last_tool_call_limit: int = 0
        self._last_tool_call_limit_reached: bool = False
        self._last_resume_attempts: int = 0

    @property
    def model(self) -> str:
        return self._model

    @property
    def last_tokens_input(self) -> int:
        return self._last_tokens_input

    @property
    def last_tokens_output(self) -> int:
        return self._last_tokens_output

    @property
    def last_tokens_thinking(self) -> int:
        return self._last_tokens_thinking

    @property
    def last_tool_call_limit(self) -> int:
        return self._last_tool_call_limit

    @property
    def last_tool_call_limit_reached(self) -> bool:
        return self._last_tool_call_limit_reached

    @property
    def last_resume_attempts(self) -> int:
        return self._last_resume_attempts

    def review(
        self,
        prompt: str,
        tools: list[Callable[..., Any]],
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    ) -> str:
        tool_entries: list[types.Tool | Callable[..., Any]] = [
            *tools,
            types.Tool(google_search=types.GoogleSearch()),
        ]
        config = types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(include_thoughts=True),
            tools=tool_entries,
            tool_config=types.ToolConfig(
                include_server_side_tool_invocations=True
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                maximum_remote_calls=max_tool_calls,
            ),
        )

        chat = self._client.chats.create(model=self._model, config=config)

        self._last_tokens_input = 0
        self._last_tokens_output = 0
        self._last_tokens_thinking = 0
        self._last_tool_call_limit = max_tool_calls
        self._last_tool_call_limit_reached = False
        self._last_resume_attempts = 0

        response = chat.send_message(prompt)
        _print_thoughts(response)
        self._add_usage(response)

        # The model can end its turn with no text — typically a final turn
        # that is only thoughts and/or a function_call. This happens when
        # automatic function calling stops on a further tool call (it has a
        # remote-call cap) instead of producing a text answer, leaving
        # ``response.text`` as None. Resume the chat so the model emits its
        # final answer as text rather than returning an empty string that
        # would crash the caller's JSON parser.
        for _ in range(_MAX_RESUME_ATTEMPTS):
            if response.text is not None:
                break
            if self._last_resume_attempts == 0:
                print(
                    f"Tool-call limit ({max_tool_calls}) reached; "
                    "resuming to elicit a text answer.",
                    file=sys.stderr,
                )
            self._last_resume_attempts += 1
            self._last_tool_call_limit_reached = True
            response = chat.send_message(_RESUME_PROMPT)
            _print_thoughts(response)
            self._add_usage(response)

        total_token_count = (
            self._last_tokens_input
            + self._last_tokens_output
            + self._last_tokens_thinking
        )
        thoughts_count = self._last_tokens_thinking
        thoughts_info = f", thinking: {thoughts_count}" if thoughts_count else ""
        resume_info = (
            f", resume_attempts: {self._last_resume_attempts}"
            if self._last_resume_attempts
            else ""
        )
        print(
            f"Tokens used — input: {self._last_tokens_input}, "
            f"output: {self._last_tokens_output}"
            f"{thoughts_info}"
            f"{resume_info}, "
            f"total: {total_token_count}",
            file=sys.stderr,
        )

        return response.text or ""

    def _add_usage(self, response: types.GenerateContentResponse) -> None:
        """Accumulate token counts from *response* into the instance counters.

        Counts are accumulated across the initial turn and any resume turns so
        the reported totals reflect the whole review.
        """
        usage = response.usage_metadata
        if usage is None:
            return
        self._last_tokens_input += usage.prompt_token_count or 0
        self._last_tokens_output += usage.candidates_token_count or 0
        self._last_tokens_thinking += usage.thoughts_token_count or 0


def _print_thoughts(response: types.GenerateContentResponse) -> None:
    """Print any thought parts from *response* to stderr.

    Also prints thoughts embedded in intermediate AFC steps, which are stored
    in ``response.automatic_function_calling_history`` as model-role
    ``Content`` objects interleaved with function-response entries.
    """
    afc_history = response.automatic_function_calling_history or []
    for content in afc_history:
        if content.role != "model" or not content.parts:
            continue
        for part in content.parts:
            if part.thought and part.text:
                print(
                    f"[Thinking (tool step)]\n{part.text.rstrip()}\n", file=sys.stderr
                )

    if not response.candidates:
        return
    content = response.candidates[0].content
    if content is None or not content.parts:
        return
    for part in content.parts:
        if part.thought and part.text:
            print(f"[Thinking]\n{part.text.rstrip()}\n", file=sys.stderr)
