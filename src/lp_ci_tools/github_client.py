from __future__ import annotations

import github


class GitHubClient:
    """GitHub client backed by PyGithub.

    Parameters
    ----------
    token:
        A GitHub personal access token.
    """

    def __init__(self, token: str) -> None:
        self._gh = github.Github(token)

    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Fetch the unified diff for a pull request.

        Uses the raw GitHub API with ``Accept: application/vnd.github.diff``
        to retrieve the diff as a string.

        Parameters
        ----------
        owner:
            The repository owner (user or organisation).
        repo:
            The repository name.
        pr_number:
            The pull request number.

        Returns
        -------
        str
            The unified diff text.
        """
        gh_repo = self._gh.get_repo(f"{owner}/{repo}")
        pr = gh_repo.get_pull(pr_number)
        # Reconstruct a unified diff from the list of changed files.
        parts: list[str] = []
        for f in pr.get_files():
            patch = f.patch
            if patch is None:
                continue
            parts.append(f"--- a/{f.filename}")
            parts.append(f"+++ b/{f.filename}")
            parts.append(patch)
        return "\n".join(parts)

    def get_pr_description(self, owner: str, repo: str, pr_number: int) -> str | None:
        """Return the body text of a pull request, or ``None`` if empty.

        Parameters
        ----------
        owner:
            The repository owner.
        repo:
            The repository name.
        pr_number:
            The pull request number.
        """
        gh_repo = self._gh.get_repo(f"{owner}/{repo}")
        pr = gh_repo.get_pull(pr_number)
        return pr.body or None

    def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: list[dict],
    ) -> None:
        """Submit a pull request review.

        Parameters
        ----------
        owner:
            The repository owner.
        repo:
            The repository name.
        pr_number:
            The pull request number.
        body:
            The general review comment body.
        comments:
            A list of inline comment dicts, each with keys ``path``,
            ``line``, and ``body``.
        """
        gh_repo = self._gh.get_repo(f"{owner}/{repo}")
        pr = gh_repo.get_pull(pr_number)

        pr.create_review(
            body=body,
            event="COMMENT",
            comments=[
                {"path": c["path"], "line": c["line"], "body": c["body"]}
                for c in comments
            ],
        )


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Extract ``(owner, repo, pr_number)`` from a GitHub PR URL.

    Accepts URLs of the form
    ``https://github.com/owner/repo/pull/42``.

    Parameters
    ----------
    url:
        The full GitHub pull request URL.

    Returns
    -------
    tuple[str, str, int]
        A three-element tuple of (owner, repo, pr_number).

    Raises
    ------
    ValueError
        If the URL does not match the expected format.
    """
    prefix = "https://github.com/"
    if not url.startswith(prefix):
        raise ValueError(
            f"Invalid GitHub PR URL (expected https://github.com/...): {url!r}"
        )

    rest = url[len(prefix) :]
    parts = rest.split("/")
    if len(parts) < 4 or parts[2] != "pull":
        raise ValueError(
            f"Invalid GitHub PR URL (expected .../owner/repo/pull/N): {url!r}"
        )

    owner = parts[0]
    repo = parts[1]
    pr_str = parts[3]

    if not owner or not repo:
        raise ValueError(f"Invalid GitHub PR URL (missing owner or repo): {url!r}")

    try:
        pr_number = int(pr_str)
    except ValueError:
        raise ValueError(
            f"Invalid GitHub PR URL (PR number {pr_str!r} is not an integer): {url!r}"
        )

    if pr_number <= 0:
        raise ValueError(
            f"Invalid GitHub PR URL (PR number must be positive, "
            f"got {pr_number}): {url!r}"
        )

    return owner, repo, pr_number
