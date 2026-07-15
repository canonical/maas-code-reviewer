# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import sys
from collections.abc import Callable
from typing import Any

from google import genai
from google.genai import types


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

    def review(self, prompt: str, tools: list[Callable[..., Any]]) -> str:
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
        )

        chat = self._client.chats.create(model=self._model, config=config)
        response = chat.send_message(prompt)

        _print_thoughts(response)

        usage = response.usage_metadata
        if usage is not None:
            self._last_tokens_input = usage.prompt_token_count or 0
            self._last_tokens_output = usage.candidates_token_count or 0
            self._last_tokens_thinking = usage.thoughts_token_count or 0
        else:
            self._last_tokens_input = 0
            self._last_tokens_output = 0
            self._last_tokens_thinking = 0

        total_token_count = (
            self._last_tokens_input
            + self._last_tokens_output
            + self._last_tokens_thinking
        )
        thoughts_count = self._last_tokens_thinking
        thoughts_info = f", thinking: {thoughts_count}" if thoughts_count else ""
        print(
            f"Tokens used — input: {self._last_tokens_input}, "
            f"output: {self._last_tokens_output}"
            f"{thoughts_info}, "
            f"total: {total_token_count}",
            file=sys.stderr,
        )

        return response.text or ""


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
