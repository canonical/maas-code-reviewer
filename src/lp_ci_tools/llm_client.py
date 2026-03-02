from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from google import genai
from google.genai import types


class LLMClient(Protocol):
    def review(self, prompt: str, tools: list[Callable[..., str]]) -> str: ...


class GeminiClient:
    """LLMClient implementation backed by the Google Gemini API.

    Uses ``chats.create`` with automatic function calling — the SDK
    handles tool dispatch, result feeding, and multi-turn looping
    internally.
    """

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "gemini-3-flash-preview",
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model = model

    def review(self, prompt: str, tools: list[Callable[..., str]]) -> str:
        config = types.GenerateContentConfig(
            tools=tools if tools else None,  # type: ignore[arg-type]
        )

        chat = self._client.chats.create(model=self._model, config=config)
        response = chat.send_message(prompt)

        return response.text or ""


def _check_protocol_compliance() -> LLMClient:
    """Purely a static type-check: GeminiClient satisfies the protocol."""
    client: LLMClient = GeminiClient(api_key="fake")
    return client
