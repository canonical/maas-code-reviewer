from __future__ import annotations

import subprocess
from pathlib import Path

from lp_ci_tools.git import GitClient, RealGitClient


class FakeGitClient:
    """GitClient backed by real git repos in temporary directories.

    This is *not* a pure in-memory fake.  It delegates to ``RealGitClient``
    for every protocol method, so it exercises the real git interaction
    without needing network access.

    Test code uses the helper functions (``create_repo``, ``add_commit``)
    to build small local repos, then calls the protocol methods against
    those repos.
    """

    def __init__(self) -> None:
        self._real = RealGitClient()

    # ------------------------------------------------------------------
    # Helpers – used by tests to set up repo state
    # ------------------------------------------------------------------

    @staticmethod
    def create_repo(path: Path, *, bare: bool = False) -> None:
        """Initialise a new git repository at *path*."""
        args = ["git", "init"]
        if bare:
            args.append("--bare")
        args.append(str(path))
        subprocess.run(args, check=True, capture_output=True)
        if not bare:
            subprocess.run(
                ["git", "config", "user.email", "test@test.com"],
                cwd=path,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"],
                cwd=path,
                check=True,
                capture_output=True,
            )

    @staticmethod
    def add_commit(
        repo: Path,
        files: dict[str, str],
        message: str = "commit",
    ) -> str:
        """Stage *files* and create a commit.  Returns the commit SHA."""
        for name, content in files.items():
            file_path = repo / name
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content)
            subprocess.run(
                ["git", "add", name],
                cwd=repo,
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["git", "commit", "-m", message],
            cwd=repo,
            check=True,
            capture_output=True,
        )
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    @staticmethod
    def create_branch(repo: Path, branch: str) -> None:
        """Create a new branch at the current HEAD."""
        subprocess.run(
            ["git", "branch", branch],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    @staticmethod
    def checkout(repo: Path, ref: str) -> None:
        """Check out *ref* (branch name or SHA)."""
        subprocess.run(
            ["git", "checkout", ref],
            cwd=repo,
            check=True,
            capture_output=True,
        )

    # ------------------------------------------------------------------
    # Protocol methods – delegated to RealGitClient
    # ------------------------------------------------------------------

    def clone(self, repo_url: str, dest: Path, branch: str) -> None:
        self._real.clone(repo_url, dest, branch)

    def diff(self, repo_dir: Path, base_ref: str, head_ref: str) -> str:
        return self._real.diff(repo_dir, base_ref, head_ref)

    def merge_into(self, repo_dir: Path, source_url: str, source_branch: str) -> None:
        self._real.merge_into(repo_dir, source_url, source_branch)

    def read_file(self, repo_dir: Path, path: str) -> str | None:
        return self._real.read_file(repo_dir, path)

    def list_changed_files(
        self, repo_dir: Path, base_ref: str, head_ref: str
    ) -> list[str]:
        return self._real.list_changed_files(repo_dir, base_ref, head_ref)


def _check_protocol_compliance() -> GitClient:
    """Purely a static type-check: FakeGitClient satisfies the protocol."""
    client: GitClient = FakeGitClient()
    return client
