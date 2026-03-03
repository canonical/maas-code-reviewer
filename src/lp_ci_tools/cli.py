from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from lp_ci_tools.git import GitClient
from lp_ci_tools.launchpad_client import LaunchpadClient
from lp_ci_tools.llm_client import GeminiClient
from lp_ci_tools.models import Comment
from lp_ci_tools.reviewer import review_diff

REVIEW_MARKER = "[lp-ci-tools review]"

_LP_GIT_BASE = "https://git.launchpad.net/"


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
        comments = client.get_comments(mp.api_url)
        last_reviewed = _find_last_review_date(comments, bot_username)
        summaries.append(
            MergeProposalSummary(
                url=mp.url,
                status=mp.status,
                last_reviewed=last_reviewed,
            )
        )
    return summaries


def has_existing_review(client: LaunchpadClient, mp_api_url: str) -> bool:
    """Return True if the bot has already posted a review on this MP."""
    comments = client.get_comments(mp_api_url)
    bot_username = client.get_bot_username()
    return _find_last_review_date(comments, bot_username) is not None


def review_merge_proposal(
    lp: LaunchpadClient,
    git: GitClient,
    llm: GeminiClient,
    mp_url: str,
    dry_run: bool = False,
) -> str | None:
    """Review a single merge proposal end to end.

    Returns the review comment body, or ``None`` if the MP was already
    reviewed.
    """
    mp = lp.get_merge_proposal(mp_url)

    if has_existing_review(lp, mp.api_url):
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

        def read_file(path: str) -> str:
            content = git.read_file(repo_dir, path)
            if content is None:
                return f"Error: file not found: {path}"
            return content

        def list_directory(path: str) -> str:
            target = repo_dir / path
            if not target.is_dir():
                return f"Error: directory not found: {path}"
            entries = sorted(entry.name for entry in target.iterdir())
            return "\n".join(entries)

        description = mp.description or mp.commit_message
        review_comment = review_diff(
            llm,
            diff=diff,
            description=description,
            read_file=read_file,
            list_directory=list_directory,
        )

    if not dry_run:
        lp.post_comment(mp.api_url, review_comment, subject="Automated review")

    return review_comment


def format_merge_proposals(summaries: list[MergeProposalSummary]) -> str:
    """Format summaries as human-readable text, one line per proposal."""
    lines = []
    for s in summaries:
        reviewed = s.last_reviewed.isoformat() if s.last_reviewed else "never"
        lines.append(f"{s.url} {s.status} {reviewed}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(1)

    if args.command == "list-merge-proposals":
        client = LaunchpadClient(credentials_file=args.launchpad_credentials)
        summaries = list_merge_proposals(client, args.project, args.status)
        output = format_merge_proposals(summaries)
        if output:
            print(output)

    elif args.command == "review":  # pragma: no cover
        lp_client = LaunchpadClient(credentials_file=args.launchpad_credentials)
        git_client = GitClient()
        api_key = Path(args.gemini_api_key_file).read_text().strip()
        llm_client = GeminiClient(api_key=api_key, model=args.model)
        result = review_merge_proposal(
            lp_client,
            git_client,
            llm_client,
            args.mp_url,
            dry_run=args.dry_run,
        )
        if result is None:
            print("Already reviewed, skipping.")
        elif args.dry_run:
            print(result)


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
        c.date
        for c in comments
        if c.author == bot_username and c.body.startswith(REVIEW_MARKER)
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
    parser = argparse.ArgumentParser(prog="lp-ci-tools")
    subparsers = parser.add_subparsers(dest="command")

    list_parser = subparsers.add_parser(
        "list-merge-proposals",
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
        "review",
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
        required=True,
        help="Path to file containing the Gemini API key.",
    )
    review_parser.add_argument(
        "--model",
        type=str,
        default="gemini-3-flash-preview",
        help="Gemini model to use (default: 'gemini-3-flash-preview').",
    )
    review_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Print review to stdout instead of posting as a comment.",
    )
    review_parser.add_argument(
        "mp_url",
        help="URL of the merge proposal to review.",
    )

    return parser
