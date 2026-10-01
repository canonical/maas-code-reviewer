# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import json
import sys
from collections.abc import Callable
from pathlib import Path

from google.genai import types

from maas_code_reviewer.llm_client import DEFAULT_MAX_TOOL_CALLS, GeminiClient
from maas_code_reviewer.metrics import ReviewMetrics
from maas_code_reviewer.review_schema import validate_review_json

REVIEW_MARKER = "[maas-code-reviewer review]"

REVIEW_PREAMBLE = """\
> LLM-generated review from https://github.com/canonical/maas-code-reviewer.
> Intended to assist a human reviewer, not replace one — suggestions may be
> incorrect, please verify before acting.
"""


class NoReviewText(Exception):
    """Raised when the LLM ends its turn without producing any review text.

    This typically happens when automatic function calling stops on a
    further tool call (the tool-call limit is reached) and, even after
    resume attempts, the model still has not emitted a text answer.
    """

_PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_prompt(*names: str) -> str:
    """Load and concatenate prompt markdown files into a single string."""
    return "\n\n".join(
        (_PROMPTS_DIR / name).read_text(encoding="utf-8").strip() for name in names
    )


STRUCTURED_SYSTEM_INSTRUCTION = _load_prompt(
    "shared_instruction.md", "structured_system_instruction.md"
)

SYSTEM_INSTRUCTION = _load_prompt("shared_instruction.md", "system_instruction.md")

STRUCTURED_INSTRUCTIONS = _load_prompt("structured_instructions.md")

INSTRUCTIONS = _load_prompt("instructions.md")

TRUNCATION_NOTE = (
    "\n\n[Note: The diff was truncated because it exceeded the maximum size. "
    "You are seeing only the first portion of the diff, ending at a complete "
    "file boundary so no hunk is left partial.]\n"
)

TRUNCATION_NOTE_MID_FILE = (
    "\n\n[Note: The diff was truncated because it exceeded the maximum size. "
    "The cut fell inside a file, so the last visible file may be partial.]\n"
)

TRUNCATION_MANIFEST_HEADER = (
    "\nThe following changed files were omitted from the truncated diff above. "
    "They ARE part of this change — use the read_file tool to inspect any of "
    "them before raising concerns that could be verified by reading the actual "
    "file content:\n"
)

EMPTY_DIFF_GENERAL_COMMENT = "No changes to review: the provided diff is empty."


def review_diff_structured(
    llm: GeminiClient,
    diff: str,
    description: str | None,
    read_file: Callable[[str], str],
    list_directory: Callable[[str], str],
    agents_md: str | None = None,
    max_diff_chars: int = 200_000,
    metrics: ReviewMetrics | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> dict:
    """Orchestrate a structured code review of *diff* using the given LLM.

    The LLM is instructed to produce a JSON object with a general comment and
    inline comments keyed by file path and line number.  A ``validate_review``
    tool is provided so the LLM can self-check its output before finalising.

    Parameters
    ----------
    llm:
        The LLM client to use for generating the review.
    diff:
        The unified diff text to review.
    description:
        The merge proposal description or commit message (may be ``None``).
    read_file:
        A callable that reads a file from the working tree.
        Signature: ``(path: str) -> str``.
    list_directory:
        A callable that lists directory contents in the working tree.
        Signature: ``(path: str) -> str``.
    agents_md:
        The contents of the repository's AGENTS.md file, if one exists, to
        be sent to the LLM up front instead of relying on a ``read_file``
        tool call (may be ``None``).
    max_diff_chars:
        Maximum number of characters for the diff before truncation.

    Returns
    -------
    dict
        The parsed JSON review object.
    """
    if _is_empty_diff(diff):
        _populate_metrics_without_llm(metrics, llm.model, diff)
        return {
            "general_comment": EMPTY_DIFF_GENERAL_COMMENT,
            "inline_comments": {},
        }

    truncated_diff = _truncate_diff(diff, max_diff_chars)
    prompt = _build_structured_prompt(truncated_diff, description)

    def validate_review(json_text: str) -> str:
        return _validate_review(json_text, truncated_diff)

    tools: list[Callable[..., str]] = [
        validate_review, read_file, list_directory
    ]
    raw_text = llm.review(
        prompt, tools, system_instruction=STRUCTURED_SYSTEM_INSTRUCTION,
        agents_md=agents_md, max_tool_calls=max_tool_calls,
    )

    _populate_metrics(metrics, llm, diff)

    cleaned = _extract_json(raw_text)
    if not cleaned:
        raise NoReviewText(
            "LLM returned no review text: the model ended its turn without "
            "producing a text answer (it may have exhausted its tool-call "
            "budget)."
        )
    result = json.loads(cleaned)
    result["general_comment"] = (
        f"{REVIEW_MARKER}\n\n{REVIEW_PREAMBLE}\n\n{result.get('general_comment', '')}"
    )
    return result


def _validate_review(json_text: str, diff_text: str) -> str:
    """Validate the review JSON against the schema and the diff.

    Returns an empty string if valid, or a newline-separated list of
    errors if invalid.
    """
    print(
        f"Tool call: validate_review(json_text=<{len(json_text)} chars>)",
        file=sys.stderr,
    )
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError as exc:
        return f"Invalid JSON: {exc}"
    errors = validate_review_json(data, diff_text)
    if errors:
        return "\n".join(errors)
    return ""


def review_diff(
    llm: GeminiClient,
    diff: str,
    description: str | None,
    read_file: Callable[[str], str],
    list_directory: Callable[[str], str],
    agents_md: str | None = None,
    max_diff_chars: int = 200_000,
    metrics: ReviewMetrics | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> str:
    """Orchestrate a code review of *diff* using the given LLM.

    Parameters
    ----------
    llm:
        The LLM client to use for generating the review.
    diff:
        The unified diff text to review.
    description:
        The merge proposal description or commit message (may be ``None``).
    read_file:
        A callable that reads a file from the merged working tree.
        Signature: ``(path: str) -> str``.
    list_directory:
        A callable that lists directory contents in the merged working tree.
        Signature: ``(path: str) -> str``.
    agents_md:
        The contents of the repository's AGENTS.md file, if one exists, to
        be sent to the LLM up front instead of relying on a ``read_file``
        tool call (may be ``None``).
    max_diff_chars:
        Maximum number of characters for the diff before truncation.

    Returns
    -------
    str
        The formatted review comment, prefixed with the review marker.
    """
    if _is_empty_diff(diff):
        _populate_metrics_without_llm(metrics, llm.model, diff)
        return f"{REVIEW_MARKER}\n\n{REVIEW_PREAMBLE}\n\n{EMPTY_DIFF_GENERAL_COMMENT}"

    truncated_diff = _truncate_diff(diff, max_diff_chars)
    prompt = _build_prompt(truncated_diff, description)

    tools: list[Callable[..., str]] = [read_file, list_directory]
    review_text = llm.review(
        prompt, tools, system_instruction=SYSTEM_INSTRUCTION,
        agents_md=agents_md, max_tool_calls=max_tool_calls,
    )

    _populate_metrics(metrics, llm, diff)

    if not review_text:
        raise NoReviewText(
            "LLM returned no review text: the model ended its turn without "
            "producing a text answer (it may have exhausted its tool-call "
            "budget)."
        )

    return f"{REVIEW_MARKER}\n\n{REVIEW_PREAMBLE}\n\n{review_text}"


def _build_structured_prompt(diff: str, description: str | None) -> list[types.Part]:
    """Construct the user-turn prompt parts for structured JSON review output.

    The system instruction (persona, task, output-format rules) is sent
    separately via ``GenerateContentConfig.system_instruction``; this returns
    only the diff, description, and per-call instructions as ``Part``s.
    """
    parts: list[types.Part] = [
        types.Part.from_text(text=f"## Diff\n\n```\n{diff}\n```\n")
    ]

    if description:
        parts.append(
            types.Part.from_text(text=f"## Description\n\n{description}\n")
        )

    parts.append(types.Part.from_text(text=STRUCTURED_INSTRUCTIONS))

    return parts


def _extract_json(text: str) -> str:
    """Extract a JSON object from *text*, stripping markdown fences if present.

    The LLM may wrap its output in a ```json ... ``` code fence.  This
    function strips such fences and returns the raw JSON string.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove the opening fence line (e.g. ```json or just ```)
        first_newline = stripped.find("\n")
        if first_newline != -1:
            stripped = stripped[first_newline + 1 :]
        # Remove the closing fence
        if stripped.endswith("```"):
            stripped = stripped[: stripped.rfind("```")]
    stripped = stripped.strip()

    candidate = _extract_first_json_object(stripped)
    if candidate is not None:
        return candidate

    return stripped


def _build_prompt(diff: str, description: str | None) -> list[types.Part]:
    """Construct the user-turn prompt parts containing the diff and description.

    The system instruction (persona, task, tool guidance) is sent
    separately via ``GenerateContentConfig.system_instruction``; this returns
    only the diff, description, and per-call instructions as ``Part``s.
    """
    parts: list[types.Part] = [
        types.Part.from_text(text=f"## Diff\n\n```\n{diff}\n```\n")
    ]

    if description:
        parts.append(
            types.Part.from_text(
                text=f"## Merge Proposal Description\n\n{description}\n"
            )
        )

    parts.append(types.Part.from_text(text=INSTRUCTIONS))

    return parts


def _extract_first_json_object(text: str) -> str | None:
    """Return the first parsable JSON object substring from *text*.

    Returns ``None`` if no JSON object can be decoded.
    """
    decoder = json.JSONDecoder()
    offset = 0

    while True:
        start = text.find("{", offset)
        if start == -1:
            return None
        try:
            _, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            offset = start + 1
            continue
        return text[start:end]


def _is_empty_diff(diff: str) -> bool:
    """Return ``True`` when *diff* has no non-whitespace content."""
    return diff.strip() == ""


def _populate_metrics_without_llm(
    metrics: ReviewMetrics | None, model_name: str, diff: str
) -> None:
    """Fill *metrics* for paths that skip the LLM call entirely."""
    if metrics is None:
        return
    metrics.model_name = model_name
    metrics.tokens_thinking = 0
    metrics.tokens_input = 0
    metrics.tokens_output = 0
    metrics.diff_lines = len(diff.splitlines())
    metrics.diff_size_bytes = len(diff.encode("utf-8"))
    metrics.tool_call_limit = 0
    metrics.tool_call_limit_reached = False
    metrics.resume_attempts = 0


def _populate_metrics(
    metrics: ReviewMetrics | None, llm: GeminiClient, diff: str
) -> None:
    """Fill *metrics* with LLM token counts and diff size info."""
    if metrics is None:
        return
    metrics.model_name = llm.model
    metrics.tokens_thinking = llm.last_tokens_thinking
    metrics.tokens_input = llm.last_tokens_input
    metrics.tokens_output = llm.last_tokens_output
    metrics.tool_call_limit = llm.last_tool_call_limit
    metrics.tool_call_limit_reached = llm.last_tool_call_limit_reached
    metrics.resume_attempts = llm.last_resume_attempts
    metrics.diff_lines = len(diff.splitlines())
    metrics.diff_size_bytes = len(diff.encode("utf-8"))


def _truncate_diff(diff: str, max_chars: int) -> str:
    """Truncate *diff* to around *max_chars*, at a file boundary.

    The cut is made at the last ``diff --git`` header that fits entirely
    before *max_chars*, so the LLM never sees a partial file/hunk.  A
    manifest of the omitted changed files is appended, listing every file
    that was cut off so the LLM can use ``read_file`` to inspect them.
    """
    if len(diff) <= max_chars:
        return diff

    # Indices (char offsets) of every "diff --git" header at the start of a
    # line.  A naive find() would match the marker inside added/removed
    # content lines (e.g. "+diff --git a/foo"), so we require the match to
    # be at position 0 or immediately after a newline.
    header_offsets: list[int] = []
    search = 0
    marker = "diff --git "
    while True:
        pos = diff.find(marker, search)
        if pos == -1:
            break
        if pos == 0 or diff[pos - 1] == "\n":
            header_offsets.append(pos)
        search = pos + len(marker)

    if not header_offsets:
        # No file headers found — fall back to a plain character cut.
        return diff[:max_chars] + TRUNCATION_NOTE_MID_FILE

    # Find the last file whose content (header to next header, or end of
    # diff) fits entirely within max_chars.  The visible portion includes
    # every fitting file in full, so no hunk is ever split.
    last_fitting_end = 0
    for i, offset in enumerate(header_offsets):
        file_end = (
            header_offsets[i + 1] if i + 1 < len(header_offsets) else len(diff)
        )
        if file_end <= max_chars:
            last_fitting_end = file_end
        else:
            break

    if last_fitting_end > 0:
        cut = last_fitting_end
        note = TRUNCATION_NOTE
    else:
        # No file fits within max_chars (e.g. a single very large file) —
        # fall back to a plain character cut, but still collect omitted
        # files for the manifest.
        cut = max_chars
        note = TRUNCATION_NOTE_MID_FILE

    visible = diff[:cut]

    # Collect the paths of files that were omitted (headers at/after the cut).
    omitted_paths: list[str] = []
    for offset in header_offsets:
        if offset < cut:
            continue
        line_end = diff.find("\n", offset)
        if line_end == -1:
            header_line = diff[offset:]
        else:
            header_line = diff[offset:line_end]
        # "diff --git a/<path> b/<path>"
        parts = header_line.split(" b/", 1)
        if len(parts) == 2:
            omitted_paths.append(parts[1])
        else:
            omitted_paths.append(header_line)

    parts: list[str] = [visible, note]
    if omitted_paths:
        parts.append(TRUNCATION_MANIFEST_HEADER)
        for path in omitted_paths:
            parts.append(f"- {path}\n")
    return "".join(parts)
