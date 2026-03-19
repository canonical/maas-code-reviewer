from __future__ import annotations

from collections.abc import Callable

from lp_ci_tools.llm_client import GeminiClient

REVIEW_MARKER = "[lp-ci-tools review]"

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
