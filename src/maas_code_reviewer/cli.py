# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn

from maas_code_reviewer.git import GitClient
from maas_code_reviewer.github_client import GitHubClient, parse_pr_url
from maas_code_reviewer.launchpad_client import LaunchpadClient
from maas_code_reviewer.llm_client import DEFAULT_MAX_TOOL_CALLS, GeminiClient
from maas_code_reviewer.metrics import ReviewMetrics, write_metrics
from maas_code_reviewer.models import Comment, MergeProposal
from maas_code_reviewer.repo_tools import RepoTools
from maas_code_reviewer.reviewer import (
    REVIEW_MARKER,
    NoReviewText,
    review_diff,
    review_diff_structured,
)

_LP_GIT_BASE = "https://git.launchpad.net/"
_MAX_TOOL_CALLS_HELP = (
    "Maximum number of automatic tool calls the LLM may make during a "
    "review (file reads, directory listings, searches). When the limit is "
    "reached the reviewer resumes the chat to elicit a text answer. "
    f"(default: {DEFAULT_MAX_TOOL_CALLS})."
)


@dataclass(frozen=True)
class MergeProposalSummary:
    url: str
    status: str
    last_reviewed: datetime | None


def list_merge_proposals(
    client: LaunchpadClient, project: str, status: str
) -> list[MergeProposalSummary]:
    """Fetch merge proposals and annotate each with its last review timestamp."""
    proposals = client.get_merge_proposals(project, status)
    bot_username = client.get_bot_username()
    summaries = []
    for mp in proposals:
        comments = client.get_comments(mp)
        last_reviewed = _find_last_review_date(comments, bot_username)
        summaries.append(
            MergeProposalSummary(
                url=mp.url,
                status=mp.status,
                last_reviewed=last_reviewed,
            )
        )
    return summaries


def has_existing_review(client: LaunchpadClient, mp: MergeProposal) -> bool:
    """Return True if the bot has already posted a review on this MP."""
    comments = client.get_comments(mp)
    bot_username = client.get_bot_username()
    return _find_last_review_date(comments, bot_username) is not None


def review_merge_proposal(
    lp: LaunchpadClient,
    git: GitClient,
    llm: GeminiClient,
    mp_url: str,
    max_diff_chars: int = 200_000,
    metrics: ReviewMetrics | None = None,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> str | None:
    """Review a single merge proposal end to end.

    Returns the review comment body, or ``None`` if the MP was already
    reviewed.  The caller is responsible for posting or printing the result.
    """
    mp = lp.get_merge_proposal(mp_url)

    if has_existing_review(lp, mp):
        return None

    target_branch = _ref_to_branch(mp.target_git_path)
    source_branch = _ref_to_branch(mp.source_git_path)
    target_url = _lp_repo_url(mp.target_git_repository)
    source_url = _lp_repo_url(mp.source_git_repository)

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        git.clone(target_url, repo_dir, target_branch)
        git.merge_into(repo_dir, source_url, source_branch)

        diff = git.diff(repo_dir, "ORIG_HEAD", "HEAD")

        tools = RepoTools(repo_dir)
        description = mp.description or mp.commit_message
        review_comment = review_diff(
            llm,
            diff=diff,
            description=description,
            read_file=tools.read_file,
            list_directory=tools.list_directory,
            max_diff_chars=max_diff_chars,
            metrics=metrics,
            max_tool_calls=max_tool_calls,
        )

        if metrics is not None:
            metrics.files_read = tools.files_read_count
            metrics.agents_md_read = tools.agents_md_read

    return review_comment


def format_merge_proposals(summaries: list[MergeProposalSummary]) -> str:
    """Format summaries as human-readable text, one line per proposal."""
    lines = []
    for s in summaries:
        reviewed = s.last_reviewed.isoformat() if s.last_reviewed else "never"
        lines.append(f"{s.url} {s.status} {reviewed}")
    return "\n".join(lines)


def handle_list_lp_mps(args: argparse.Namespace) -> None:
    """Handle the list-lp-mps subcommand."""
    client = LaunchpadClient(credentials_file=args.launchpad_credentials)
    summaries = list_merge_proposals(client, args.project, args.status)
    output = format_merge_proposals(summaries)
    if output:
        print(output)


def handle_review_mp(args: argparse.Namespace) -> None:
    """Handle the review-mp subcommand."""
    api_key = _get_gemini_api_key(args)
    if api_key is None:
        _exit_missing_gemini_api_key()
    lp_client = LaunchpadClient(credentials_file=args.launchpad_credentials)
    git_client = GitClient()
    llm_client = GeminiClient(api_key=api_key, model=args.model)
    metrics = ReviewMetrics()
    result = review_merge_proposal(
        lp_client,
        git_client,
        llm_client,
        args.mp_url,
        max_diff_chars=args.max_diff_chars,
        metrics=metrics,
        max_tool_calls=args.max_tool_calls,
    )
    if args.metrics is not None and result is not None:
        write_metrics(metrics, Path(args.metrics))
    if result is None:
        print("Already reviewed, skipping.")
    elif args.dry_run:
        print(result)
    else:
        mp = lp_client.get_merge_proposal(args.mp_url)
        lp_client.post_comment(mp, result, subject="Automated review")


def handle_review_diff(args: argparse.Namespace) -> None:
    """Handle the review-diff subcommand."""
    api_key = _get_gemini_api_key(args)
    if api_key is None:
        _exit_missing_gemini_api_key()

    if args.diff_file == "-":
        diff = sys.stdin.read()
    else:
        diff = Path(args.diff_file).read_text()

    repo_dir = Path(args.repo_dir) if args.repo_dir else Path.cwd()

    llm_client = GeminiClient(api_key=api_key, model=args.model)

    tools = RepoTools(repo_dir)
    metrics = ReviewMetrics()

    if args.json_output:
        result_dict = review_diff_structured(
            llm_client,
            diff=diff,
            description=None,
            read_file=tools.read_file,
            list_directory=tools.list_directory,
            max_diff_chars=args.max_diff_chars,
            metrics=metrics,
            max_tool_calls=args.max_tool_calls,
        )
        Path(args.json_output).write_text(json.dumps(result_dict, indent=2))
    else:
        result = review_diff(
            llm_client,
            diff=diff,
            description=None,
            read_file=tools.read_file,
            list_directory=tools.list_directory,
            max_diff_chars=args.max_diff_chars,
            metrics=metrics,
            max_tool_calls=args.max_tool_calls,
        )
        print(result)

    metrics.files_read = tools.files_read_count
    metrics.agents_md_read = tools.agents_md_read
    if args.metrics is not None:
        write_metrics(metrics, Path(args.metrics))


def handle_review_pr(args: argparse.Namespace) -> None:
    """Handle the review-pr subcommand."""
    owner, repo, pr_number = parse_pr_url(args.pr_url)

    token = args.github_token or os.environ.get("GITHUB_TOKEN")
    if not token:
        print(
            "Error: GitHub token not provided. Use --github-token or set the "
            "GITHUB_TOKEN environment variable.",
            file=sys.stderr,
        )
        sys.exit(1)

    api_key = _get_gemini_api_key(args)
    if api_key is None:
        _exit_missing_gemini_api_key()

    github_client = GitHubClient(token)
    diff = github_client.get_pr_diff(owner, repo, pr_number)
    description = github_client.get_pr_description(owner, repo, pr_number)

    repo_dir = Path(args.repo_dir) if args.repo_dir else Path.cwd()
    tools = RepoTools(repo_dir)

    llm_client = GeminiClient(api_key=api_key, model=args.model)

    metrics = ReviewMetrics()
    result_dict = review_diff_structured(
        llm_client,
        diff=diff,
        description=description,
        read_file=tools.read_file,
        list_directory=tools.list_directory,
        max_diff_chars=args.max_diff_chars,
        metrics=metrics,
        max_tool_calls=args.max_tool_calls,
    )

    metrics.files_read = tools.files_read_count
    metrics.agents_md_read = tools.agents_md_read
    if args.metrics is not None:
        write_metrics(metrics, Path(args.metrics))

    if args.dry_run:
        print(json.dumps(result_dict, indent=2))
        return

    general_comment = result_dict.get("general_comment", "")
    inline_comments = result_dict.get("inline_comments", {})

    comments = [
        {"path": file_path, "line": int(line_str), "body": comment_body}
        for file_path, line_map in inline_comments.items()
        for line_str, comment_body in line_map.items()
    ]

    github_client.post_review(
        owner, repo, pr_number, body=general_comment, comments=comments
    )


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)
    try:
        if args.command == "list-lp-mps":
            handle_list_lp_mps(args)
        elif args.command == "review-mp":
            handle_review_mp(args)
        elif args.command == "review-diff":
            handle_review_diff(args)
        elif args.command == "review-pr":
            handle_review_pr(args)
    except NoReviewText as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def _get_gemini_api_key(args: argparse.Namespace) -> str | None:
    """Return the Gemini API key from --gemini-api-key-file or the environment.

    Returns ``None`` if neither source provides a non-empty key.
    """
    if args.gemini_api_key_file:
        path = Path(args.gemini_api_key_file)
        if not path.is_file():
            return None
        return path.read_text().strip() or None
    return os.environ.get("GEMINI_API_KEY", "").strip() or None


def _exit_missing_gemini_api_key() -> NoReturn:
    """Print an error message about a missing Gemini API key and exit."""
    print(
        "Error: No Gemini API key configured. Use --gemini-api-key-file or set "
        "the GEMINI_API_KEY environment variable.",
        file=sys.stderr,
    )
    sys.exit(1)


def _lp_repo_url(unique_name: str) -> str:
    """Convert a Launchpad repo unique name to a git clone URL.

    If the provided name already looks like an absolute path or URL
    (leading '/', 'file://', 'http://', or 'https://'), return it unchanged.
    """
    if (
        unique_name.startswith("/")
        or unique_name.startswith("file://")
        or unique_name.startswith("http://")
        or unique_name.startswith("https://")
    ):
        return unique_name
    return _LP_GIT_BASE + unique_name


def _find_last_review_date(
    comments: list[Comment], bot_username: str
) -> datetime | None:
    """Find the timestamp of the most recent review comment by the bot."""
    review_dates = [
        comment.date
        for comment in comments
        if comment.author == bot_username and comment.body.startswith(REVIEW_MARKER)
    ]
    if not review_dates:
        return None
    return max(review_dates)


def _ref_to_branch(git_path: str) -> str:
    """Convert a refs/heads/branch-name path to just the branch name."""
    prefix = "refs/heads/"
    if git_path.startswith(prefix):
        return git_path[len(prefix) :]
    return git_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="maas-code-reviewer")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser(
        "list-lp-mps",
        help="List merge proposals for a project.",
    )
    list_parser.add_argument(
        "--launchpad-credentials",
        type=str,
        default=None,
        help="Path to Launchpad credentials file.",
    )
    list_parser.add_argument(
        "--status",
        default="Needs review",
        help="Filter merge proposals by status (default: 'Needs review').",
    )
    list_parser.add_argument(
        "project",
        help="Launchpad project name.",
    )

    review_parser = subparsers.add_parser(
        "review-mp",
        help="Review a single merge proposal.",
    )
    review_parser.add_argument(
        "--launchpad-credentials",
        type=str,
        default=None,
        help="Path to Launchpad credentials file.",
    )
    review_parser.add_argument(
        "-g",
        "--gemini-api-key-file",
        type=str,
        default=None,
        help=(
            "Path to file containing the Gemini API key. If not provided, "
            "read from the GEMINI_API_KEY environment variable."
        ),
    )
    review_parser.add_argument(
        "--model",
        type=str,
        default="gemini-3-flash-preview",
        help="Gemini model to use (default: 'gemini-3-flash-preview').",
    )
    review_parser.add_argument(
        "--max-diff-chars",
        type=int,
        default=200_000,
        metavar="N",
        help=(
            "Maximum diff size in characters before truncation "
            "(default: 200000)."
        ),
    )
    review_parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=DEFAULT_MAX_TOOL_CALLS,
        metavar="N",
        help=_MAX_TOOL_CALLS_HELP,
    )
    review_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print review to stdout instead of posting as a comment.",
    )
    review_parser.add_argument(
        "--metrics",
        type=str,
        default=None,
        metavar="FILE",
        help="Write review metrics (model, tokens, diff stats) as JSON to FILE.",
    )
    review_parser.add_argument(
        "mp_url",
        help="URL of the merge proposal to review.",
    )

    diff_parser = subparsers.add_parser(
        "review-diff",
        help="Review a unified diff file and print the result to stdout.",
    )
    diff_parser.add_argument(
        "-g",
        "--gemini-api-key-file",
        type=str,
        default=None,
        help=(
            "Path to file containing the Gemini API key. If not provided, "
            "read from the GEMINI_API_KEY environment variable."
        ),
    )
    diff_parser.add_argument(
        "--model",
        type=str,
        default="gemini-3-flash-preview",
        help="Gemini model to use (default: 'gemini-3-flash-preview').",
    )
    diff_parser.add_argument(
        "--max-diff-chars",
        type=int,
        default=200_000,
        metavar="N",
        help=(
            "Maximum diff size in characters before truncation "
            "(default: 200000)."
        ),
    )
    diff_parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=DEFAULT_MAX_TOOL_CALLS,
        metavar="N",
        help=_MAX_TOOL_CALLS_HELP,
    )
    diff_parser.add_argument(
        "--repo-dir",
        type=str,
        default=None,
        help=(
            "Path to the local git repository (default: current working directory). "
            "Used for read_file and list_directory tool calls."
        ),
    )
    diff_parser.add_argument(
        "--json-output",
        type=str,
        default=None,
        metavar="FILE",
        help=(
            "Write structured JSON review output to FILE instead of plain text to "
            "stdout. The JSON contains a 'general_comment' and 'inline_comments' "
            "keyed by file path and line number."
        ),
    )
    diff_parser.add_argument(
        "--metrics",
        type=str,
        default=None,
        metavar="FILE",
        help="Write review metrics (model, tokens, diff stats) as JSON to FILE.",
    )
    diff_parser.add_argument(
        "diff_file",
        help="Path to a unified diff file, or '-' to read from stdin.",
    )

    pr_parser = subparsers.add_parser(
        "review-pr",
        help="Review a GitHub pull request and post the review.",
    )
    pr_parser.add_argument(
        "-g",
        "--gemini-api-key-file",
        type=str,
        default=None,
        help=(
            "Path to file containing the Gemini API key. If not provided, "
            "read from the GEMINI_API_KEY environment variable."
        ),
    )
    pr_parser.add_argument(
        "--github-token",
        type=str,
        default=None,
        help=(
            "GitHub personal access token. If not provided, read from the "
            "GITHUB_TOKEN environment variable."
        ),
    )
    pr_parser.add_argument(
        "--model",
        type=str,
        default="gemini-3-flash-preview",
        help="Gemini model to use (default: 'gemini-3-flash-preview').",
    )
    pr_parser.add_argument(
        "--max-diff-chars",
        type=int,
        default=200_000,
        metavar="N",
        help=(
            "Maximum diff size in characters before truncation "
            "(default: 200000)."
        ),
    )
    pr_parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=DEFAULT_MAX_TOOL_CALLS,
        metavar="N",
        help=_MAX_TOOL_CALLS_HELP,
    )
    pr_parser.add_argument(
        "--repo-dir",
        type=str,
        default=None,
        help=(
            "Path to a local checkout of the repository (default: current working "
            "directory). Used for read_file and list_directory tool calls."
        ),
    )
    pr_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print the review JSON to stdout instead of posting it.",
    )
    pr_parser.add_argument(
        "--metrics",
        type=str,
        default=None,
        metavar="FILE",
        help="Write review metrics (model, tokens, diff stats) as JSON to FILE.",
    )
    pr_parser.add_argument(
        "pr_url",
        help="Full GitHub PR URL, e.g. https://github.com/owner/repo/pull/42.",
    )

    return parser
