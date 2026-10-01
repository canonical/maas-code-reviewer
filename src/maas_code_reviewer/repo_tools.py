# Copyright 2026 Canonical Ltd.  This software is licensed under the
# GNU Affero General Public License version 3 (see the file LICENSE).

import sys
from pathlib import Path


class RepoTools:
    """File-system tools scoped to a single repository directory.

    All paths are resolved and checked against ``repo_dir`` before any
    operation is performed, so neither ``read_file`` nor ``list_directory``
    can be used to escape outside the repository tree.
    """

    def __init__(self, repo_dir: Path) -> None:
        self._repo_dir = repo_dir.resolve()
        self.files_read_count: int = 0
        self.agents_md_read: bool = False

    def read_agents_md(self) -> str | None:
        """Return the contents of the repo's top-level AGENTS.md, or None.

        Used to proactively include project conventions in the review
        prompt instead of relying on the model to request the file via the
        read_file tool. Sets ``agents_md_read`` on success, matching the
        flag set by ``read_file`` so metrics stay accurate either way.
        """
        target = (self._repo_dir / "AGENTS.md").resolve()
        if not target.is_relative_to(self._repo_dir):
            return None
        if not target.is_file():
            return None
        self.agents_md_read = True
        return target.read_text(encoding="utf-8")

    def read_file(self, path: str) -> str:
        print(f"Tool call: read_file(path={path!r})", file=sys.stderr)
        target = (self._repo_dir / path).resolve()
        if not target.is_relative_to(self._repo_dir):
            return f"Error: path outside repository: {path}"
        if not target.is_file():
            return f"Error: file not found: {path}"
        self.files_read_count += 1
        if target.name == "AGENTS.md":
            self.agents_md_read = True
        return target.read_text()

    def list_directory(self, path: str) -> str:
        print(f"Tool call: list_directory(path={path!r})", file=sys.stderr)
        target = (self._repo_dir / path).resolve()
        if not target.is_relative_to(self._repo_dir):
            return f"Error: path outside repository: {path}"
        if not target.is_dir():
            return f"Error: directory not found: {path}"
        entries = sorted(entry.name for entry in target.iterdir())
        return "\n".join(entries)
