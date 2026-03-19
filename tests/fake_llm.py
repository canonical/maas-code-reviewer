from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from google.genai import types

from maas_code_reviewer.llm_client import GeminiClient


@dataclass
class ToolCall:
    """A tool invocation the fake should perform before returning."""

    name: str
    args: dict[str, str]


@dataclass
class ScriptedResponse:
    """A single scripted response, optionally preceded by tool calls."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class _FakeChat:
    """Fake ``google.genai.chats.Chat`` that executes scripted responses.

    Each call to ``send_message`` pops the next ``ScriptedResponse`` from
    the queue.  If the response includes ``tool_calls``, the fake invokes
    the corresponding tools (looked up by name in the config) with the
    given arguments before returning a response whose ``.text`` is the
    scripted text.
    """

    def __init__(
        self,
        responses: list[ScriptedResponse],
        config: types.GenerateContentConfig | None,
        owner: FakeGenaiClient,
    ) -> None:
        self._responses = responses
        self._owner = owner
        self._tools_by_name: dict[str, Callable[..., str]] = {}
        if config and config.tools:
            for tool in config.tools:
                if callable(tool):
                    self._tools_by_name[tool.__name__] = tool

    def send_message(self, message: str) -> types.GenerateContentResponse:
        self._owner.received_prompts.append(message)
        self._owner.received_tools.append(list(self._tools_by_name.values()))

        if not self._responses:
            raise RuntimeError("FakeGenaiClient: no more scripted responses")

        response = self._responses.pop(0)

        for tc in response.tool_calls:
            fn = self._tools_by_name.get(tc.name)
            if fn is None:
                raise RuntimeError(
                    f"FakeGenaiClient: tool '{tc.name}' not found in provided tools"
                )
            fn(**tc.args)

        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(
                    content=types.Content(
                        parts=[types.Part(text=response.text)],
                        role="model",
                    ),
                ),
            ],
        )


class _FakeChats:
    """Fake ``google.genai.Client.chats`` namespace."""

    def __init__(
        self, responses: list[ScriptedResponse], owner: FakeGenaiClient
    ) -> None:
        self._responses = responses
        self._owner = owner

    def create(
        self,
        *,
        model: str,
        config: types.GenerateContentConfig | None = None,
    ) -> _FakeChat:
        return _FakeChat(self._responses, config, self._owner)


class FakeGenaiClient:
    """Drop-in fake for ``google.genai.Client``.

    Provides a ``.chats`` attribute whose ``.create()`` method returns a
    ``_FakeChat`` that pops scripted responses.

    After use, ``received_prompts`` and ``received_tools`` expose what
    was passed to each ``send_message()`` call for assertion.
    """

    def __init__(self, responses: list[ScriptedResponse] | None = None) -> None:
        self.received_prompts: list[str] = []
        self.received_tools: list[list[Callable[..., str]]] = []
        self.chats = _FakeChats(list(responses) if responses else [], self)


def FakeLLMClient(
    responses: list[ScriptedResponse] | None = None,
) -> GeminiClient:
    """Build a ``GeminiClient`` backed by a ``FakeGenaiClient``.

    This exercises the real ``GeminiClient.review()`` code path while
    allowing tests to script the responses returned by the underlying
    genai client.

    The ``FakeGenaiClient`` is accessible via the returned client's
    ``_client`` attribute for test assertions on ``received_prompts``
    and ``received_tools``.
    """
    fake_genai = FakeGenaiClient(responses)
    return GeminiClient(client=fake_genai)  # type: ignore[arg-type]
