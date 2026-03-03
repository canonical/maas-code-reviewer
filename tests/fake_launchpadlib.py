"""Fake that mimics the launchpadlib object model.

``LaunchpadClient`` talks to launchpadlib objects (``Launchpad``,
project entries, merge-proposal entries, comment entries).  This module
provides in-memory fakes for all of those so that tests can exercise
``LaunchpadClient`` without hitting the network.

Usage in tests::

    fake_lp = FakeLaunchpad(bot_username="ci-bot")
    # … populate with add_project / add_merge_proposal / add_comment …

    with fake_lp.patch_login_with():
        client = LaunchpadClient(credentials_file="/some/creds")
        # client now operates against the fake data
    assert fake_lp.credentials_file == "/some/creds"
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from unittest.mock import patch

from lp_ci_tools.launchpad_client import web_url_to_api_url

# ------------------------------------------------------------------
# Leaf fakes – these mimic the attribute-access API that
# ``LaunchpadClient`` uses on launchpadlib entry objects.
# ------------------------------------------------------------------


@dataclass
class FakeUser:
    name: str
    display_name: str = ""

    def __post_init__(self) -> None:
        if not self.display_name:
            self.display_name = self.name


@dataclass
class FakeGitRepository:
    unique_name: str


@dataclass
class FakeComment:
    author: FakeUser
    author_link: str
    message_body: str
    date_created: datetime


@dataclass
class FakeMergeProposal:
    web_link: str
    self_link: str
    source_git_repository: FakeGitRepository
    source_git_repository_link: str
    source_git_path: str
    target_git_repository: FakeGitRepository
    target_git_repository_link: str
    target_git_path: str
    queue_status: str
    commit_message: str | None = None
    description: str | None = None
    all_comments: list[FakeComment] = field(default_factory=list)
    _owner: FakeUser | None = field(default=None, repr=False)

    def createComment(self, *, subject: str, content: str) -> None:
        author = self._owner or FakeUser(name="unknown")
        comment = FakeComment(
            author=author,
            author_link=_SERVICE_ROOT + "~" + author.name,
            message_body=content,
            date_created=datetime(2025, 1, 1, tzinfo=UTC),
        )
        self.all_comments.append(comment)

    def getMergeProposals(self, status: str) -> list[FakeMergeProposal]:
        """Only here so the type looks right; real filtering is on FakeProject."""
        raise NotImplementedError


@dataclass
class FakeProject:
    name: str
    _merge_proposals: list[FakeMergeProposal] = field(default_factory=list)

    def getMergeProposals(self, status: str) -> list[FakeMergeProposal]:
        return [mp for mp in self._merge_proposals if mp.queue_status == status]


# ------------------------------------------------------------------
# Top-level fake – stands in for the ``Launchpad`` instance returned
# by ``Launchpad.login_with()``.
# ------------------------------------------------------------------

_SERVICE_ROOT = "https://api.launchpad.net/devel/"


class FakeLaunchpad:
    """In-memory replacement for a ``launchpadlib.launchpad.Launchpad`` instance.

    Populate it with ``add_project``, ``add_merge_proposal``, and
    ``add_comment``, then pass it to ``LaunchpadClient`` (by
    patching ``Launchpad.login_with`` to return this object).
    """

    def __init__(self, bot_username: str = "review-bot") -> None:
        self.me = FakeUser(name=bot_username)
        self.credentials_file: str | None = None
        self._projects: dict[str, FakeProject] = {}
        # self_link -> FakeMergeProposal (for lp.load on API URLs)
        self._merge_proposals: dict[str, FakeMergeProposal] = {}

    # ------------------------------------------------------------------
    # Helpers – used by tests to populate internal state
    # ------------------------------------------------------------------

    def add_project(self, name: str) -> FakeProject:
        project = FakeProject(name=name)
        self._projects[name] = project
        return project

    def add_merge_proposal(self, project_name: str, mp: FakeMergeProposal) -> None:
        if project_name not in self._projects:
            self.add_project(project_name)
        mp._owner = self.me
        self._projects[project_name]._merge_proposals.append(mp)
        self._merge_proposals[mp.self_link] = mp

    def add_comment(self, mp_web_link: str, comment: FakeComment) -> None:
        # Look up by web_link for test convenience
        for mp in self._merge_proposals.values():
            if mp.web_link == mp_web_link:
                mp.all_comments.append(comment)
                return
        raise KeyError(f"No merge proposal with web_link {mp_web_link!r}")

    # ------------------------------------------------------------------
    # launchpadlib API surface used by LaunchpadClient
    # ------------------------------------------------------------------

    def load(self, url: str) -> FakeProject | FakeMergeProposal:
        # LaunchpadClient calls lp.load(_SERVICE_ROOT + project_name)
        # for projects, and lp.load(mp_url) for merge proposals.
        # Check merge proposals first since their self_links also start
        # with _SERVICE_ROOT.
        if url in self._merge_proposals:
            return self._merge_proposals[url]
        if url.startswith(_SERVICE_ROOT):
            project_name = url[len(_SERVICE_ROOT) :]
            return self._projects[project_name]
        raise KeyError(f"Nothing loaded at {url!r}")

    @contextmanager
    def patch_login_with(self) -> Iterator[None]:
        """Patch ``Launchpad.login_with`` to return this fake.

        The credentials file passed by the caller is recorded in
        ``self.credentials_file`` so tests can assert on it.
        """

        def _fake_login_with(
            *_args: object, credentials_file: str | None = None, **_kwargs: object
        ) -> FakeLaunchpad:
            self.credentials_file = credentials_file
            return self

        with patch(
            "lp_ci_tools.launchpad_client.Launchpad.login_with",
            side_effect=_fake_login_with,
        ):
            yield


def make_fake_mp(
    *,
    web_link: str = "https://code.launchpad.net/~user/project/+git/repo/+merge/1",
    self_link: str | None = None,
    source_repo: str = "~user/project/+git/repo",
    source_path: str = "refs/heads/feature",
    target_repo: str = "~user/project/+git/repo",
    target_path: str = "refs/heads/main",
    status: str = "Needs review",
    commit_message: str | None = None,
    description: str | None = None,
) -> FakeMergeProposal:
    """Convenience factory for ``FakeMergeProposal``."""
    return FakeMergeProposal(
        web_link=web_link,
        self_link=self_link if self_link is not None else web_url_to_api_url(web_link),
        source_git_repository=FakeGitRepository(unique_name=source_repo),
        source_git_repository_link=_SERVICE_ROOT + source_repo,
        source_git_path=source_path,
        target_git_repository=FakeGitRepository(unique_name=target_repo),
        target_git_repository_link=_SERVICE_ROOT + target_repo,
        target_git_path=target_path,
        queue_status=status,
        commit_message=commit_message,
        description=description,
    )


def make_fake_comment(
    *,
    author: str = "someone",
    body: str = "A comment",
    date: datetime | None = None,
) -> FakeComment:
    """Convenience factory for ``FakeComment``."""
    return FakeComment(
        author=FakeUser(name=author),
        author_link=_SERVICE_ROOT + "~" + author,
        message_body=body,
        date_created=date or datetime(2025, 1, 1, tzinfo=UTC),
    )
