from __future__ import annotations

from pathlib import Path


class RepoTools:
    """File-system tools scoped to a single repository directory.

    All paths are resolved and checked against ``repo_dir`` before any
    operation is performed, so neither ``read_file`` nor ``list_directory``
    can be used to escape outside the repository tree.
    """

    def __init__(self, repo_dir: Path) -> None:
        self._repo_dir = repo_dir.resolve()

    def read_file(self, path: str) -> str:
        target = (self._repo_dir / path).resolve()
        if not target.is_relative_to(self._repo_dir):
            return f"Error: path outside repository: {path}"
        if not target.is_file():
            return f"Error: file not found: {path}"
        return target.read_text()

    def list_directory(self, path: str) -> str:
        target = (self._repo_dir / path).resolve()
        if not target.is_relative_to(self._repo_dir):
            return f"Error: path outside repository: {path}"
        if not target.is_dir():
            return f"Error: directory not found: {path}"
        entries = sorted(entry.name for entry in target.iterdir())
        return "\n".join(entries)
