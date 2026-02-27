from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class MergeProposal:
    url: str
    source_git_repository: str
    source_git_path: str
    target_git_repository: str
    target_git_path: str
    status: str
    commit_message: str | None
    description: str | None


@dataclass(frozen=True)
class Comment:
    author: str
    body: str
    date: datetime
