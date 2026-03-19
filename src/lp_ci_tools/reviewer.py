from __future__ import annotations

import json
from collections.abc import Callable

from lp_ci_tools.llm_client import GeminiClient
from lp_ci_tools.review_schema import validate_review_json

REVIEW_MARKER = "[lp-ci-tools review]"

STRUCTURED_SYSTEM_INSTRUCTION = """\
You are an experienced software engineer performing a code review. Your job is to:

1. Identify bugs, logic errors, and potential issues.
2. Suggest improvements for readability, maintainability, and performance.
3. Point out any security concerns.
4. Be constructive and specific — reference file paths and line numbers when \
possible.

You are provided with the diff of the proposed changes. If you need more \
context (e.g. to understand how a changed function is used elsewhere, or to \
read project conventions from an AGENTS.md file), use the provided tools \
to read files or list directory contents in the merged working tree.

You MUST produce your review as a JSON object matching this schema:

{
  "general_comment": "<overall review as a string>",
  "inline_comments": {
    "<file path>": {
      "<line number as string>": "<comment text>",
      ...
    },
    ...
  }
}

Rules for inline_comments:
- Only include file paths that appear in the diff.
- Only include line numbers that appear in the diff for that file (use the \
new-file line numbers from the hunk headers).
- Line numbers must be JSON string keys (e.g. "42", not 42).
- If you have no inline comments, use an empty object {}.

Before finalising your response, call the validate_review tool with your JSON \
to check it against the schema and the diff. Fix any errors it reports and \
re-validate until there are no errors. Then output the final JSON object and \
nothing else.\
"""

SYSTEM_INSTRUCTION = """\
You are an experienced software engineer performing a code review on a merge \
proposal. Your job is to:

1. Identify bugs, logic errors, and potential issues.
2. Suggest improvements for readability, maintainability, and performance.
3. Point out any security concerns.
4. Be constructive and specific — reference file paths and line numbers when \
possible.

You are provided with the diff of the proposed changes. If you need more \
context (e.g. to understand how a changed function is used elsewhere, or to \
read project conventions from an AGENTS.md file), use the provided tools \
to read files or list directory contents in the merged working tree.

Keep your review concise and actionable. Do not repeat the diff back. \
Focus on what matters.\
"""

TRUNCATION_NOTE = (
    "\n\n[Note: The diff was truncated because it exceeded the maximum size. "
    "You are seeing a partial diff.]\n"
)


def review_diff_structured(
    llm: GeminiClient,
    diff: str,
    description: str | None,
    read_file: Callable[[str], str],
    list_directory: Callable[[str], str],
    max_diff_chars: int = 30_000,
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
    max_diff_chars:
        Maximum number of characters for the diff before truncation.

    Returns
    -------
    dict
        The parsed JSON review object.
    """
    truncated_diff = _truncate_diff(diff, max_diff_chars)
    prompt = _build_structured_prompt(truncated_diff, description)

    def validate_review(json_text: str) -> str:
        """Validate the review JSON against the schema and the diff.

        Returns an empty string if valid, or a newline-separated list of
        errors if invalid.
        """
        try:
            data = json.loads(json_text)
        except json.JSONDecodeError as exc:
            return f"Invalid JSON: {exc}"
        errors = validate_review_json(data, truncated_diff)
        if errors:
            return "\n".join(errors)
        return ""

    tools: list[Callable[..., str]] = [validate_review, read_file, list_directory]
    raw_text = llm.review(prompt, tools)

    cleaned = _extract_json(raw_text)
    return json.loads(cleaned)


def review_diff(
    llm: GeminiClient,
    diff: str,
    description: str | None,
    read_file: Callable[[str], str],
    list_directory: Callable[[str], str],
    max_diff_chars: int = 30_000,
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
    max_diff_chars:
        Maximum number of characters for the diff before truncation.

    Returns
    -------
    str
        The formatted review comment, prefixed with the review marker.
    """
    truncated_diff = _truncate_diff(diff, max_diff_chars)
    prompt = _build_prompt(truncated_diff, description)

    tools: list[Callable[..., str]] = [read_file, list_directory]
    review_text = llm.review(prompt, tools)

    return f"{REVIEW_MARKER}\n\n{review_text}"


def _build_structured_prompt(diff: str, description: str | None) -> str:
    """Construct the prompt for structured JSON review output."""
    parts: list[str] = [
        STRUCTURED_SYSTEM_INSTRUCTION,
        "\n\n## Diff\n\n```\n",
        diff,
        "\n```\n",
    ]

    if description:
        parts.append("\n## Description\n\n")
        parts.append(description)
        parts.append("\n")

    parts.append(
        "\n## Instructions\n\n"
        "Review the diff above. Use the provided tools to read files or list "
        "directories if you need additional context. Call validate_review with "
        "your JSON before finalising. Output only the final JSON object."
    )

    return "".join(parts)


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
    return stripped.strip()


def _build_prompt(diff: str, description: str | None) -> str:
    """Construct the full prompt from the system instruction, diff, and description."""
    parts: list[str] = [SYSTEM_INSTRUCTION, "\n\n## Diff\n\n```\n", diff, "\n```\n"]

    if description:
        parts.append("\n## Merge Proposal Description\n\n")
        parts.append(description)
        parts.append("\n")

    parts.append(
        "\n## Instructions\n\n"
        "Review the diff above. Use the provided tools to read files or list "
        "directories if you need additional context. Provide your review."
    )

    return "".join(parts)


def _truncate_diff(diff: str, max_chars: int) -> str:
    """Truncate *diff* to *max_chars*, appending a note if truncated."""
    if len(diff) <= max_chars:
        return diff
    return diff[:max_chars] + TRUNCATION_NOTE
