from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FakePullRequest:
    """In-memory representation of a GitHub pull request."""

    owner: str
    repo: str
    number: int
    diff: str
    description: str | None = None
    posted_reviews: list[dict] = field(default_factory=list)


class FakeGitHubClient:
    """Fake implementation of GitHubClient backed by in-memory state.

    Store PR data using ``add_pull_request()`` before running code under test.
    Inspect ``posted_reviews`` on the returned ``FakePullRequest`` (or via
    ``get_posted_reviews()``) after the test to assert on what was posted.
    """

    def __init__(self) -> None:
        self._pull_requests: dict[tuple[str, str, int], FakePullRequest] = {}

    def add_pull_request(
        self,
        owner: str,
        repo: str,
        number: int,
        diff: str,
        description: str | None = None,
    ) -> FakePullRequest:
        """Register a fake PR so the client can serve it during tests."""
        pr = FakePullRequest(
            owner=owner,
            repo=repo,
            number=number,
            diff=diff,
            description=description,
        )
        self._pull_requests[(owner, repo, number)] = pr
        return pr

    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Return the diff registered for the given PR."""
        pr = self._pull_requests[(owner, repo, pr_number)]
        return pr.diff

    def get_pr_description(self, owner: str, repo: str, pr_number: int) -> str | None:
        """Return the description registered for the given PR."""
        pr = self._pull_requests[(owner, repo, pr_number)]
        return pr.description

    def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: list[dict],
    ) -> None:
        """Record the posted review on the corresponding FakePullRequest."""
        pr = self._pull_requests[(owner, repo, pr_number)]
        pr.posted_reviews.append({"body": body, "comments": comments})

    def get_posted_reviews(self, owner: str, repo: str, pr_number: int) -> list[dict]:
        """Convenience accessor for the reviews posted to a specific PR."""
        pr = self._pull_requests[(owner, repo, pr_number)]
        return pr.posted_reviews
