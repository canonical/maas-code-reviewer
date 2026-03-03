from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class MergeProposal:
    url: str
    api_url: str
    source_git_repository: str
    source_git_path: str
    target_git_repository: str
    target_git_path: str
    status: str
    commit_message: str | None
    description: str | None
    _lp_object: object = field(compare=False, hash=False, repr=False)


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    date: datetime
