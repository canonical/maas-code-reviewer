from __future__ import annotations

import subprocess
from pathlib import Path


class GitClient:
    """GitClient implementation that wraps subprocess calls to git."""

    def clone(self, repo_url: str, dest: Path, branch: str) -> None:
        subprocess.run(
            ["git", "clone", "--branch", branch, repo_url, str(dest)],
            check=True,
            capture_output=True,
        )

    def diff(self, repo_dir: Path, base_ref: str, head_ref: str) -> str:
        result = subprocess.run(
            ["git", "diff", base_ref, head_ref],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def merge_into(self, repo_dir: Path, source_url: str, source_branch: str) -> None:
        subprocess.run(
            ["git", "fetch", source_url, source_branch],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "merge", "FETCH_HEAD", "--no-edit"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
        )

    def read_file(self, repo_dir: Path, path: str) -> str | None:
        file_path = repo_dir / path
        if not file_path.is_file():
            return None
        return file_path.read_text()

    def list_changed_files(
        self, repo_dir: Path, base_ref: str, head_ref: str
    ) -> list[str]:
        result = subprocess.run(
            ["git", "diff", "--name-only", base_ref, head_ref],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        if not result.stdout.strip():
            return []
        return result.stdout.strip().split("\n")
