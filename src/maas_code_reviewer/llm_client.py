from __future__ import annotations

from collections.abc import Callable

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

    def review(self, prompt: str, tools: list[Callable[..., str]]) -> str:
        config = types.GenerateContentConfig(
            tools=tools if tools else None,  # type: ignore[arg-type]
        )

        chat = self._client.chats.create(model=self._model, config=config)
        response = chat.send_message(prompt)

        return response.text or ""
