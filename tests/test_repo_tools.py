from __future__ import annotations

from pathlib import Path

from lp_ci_tools.repo_tools import RepoTools


class TestRepoTools:
    def test_read_file_returns_content(self, tmp_path: Path) -> None:
        """read_file returns the content of a file inside the repository."""
        (tmp_path / "notes.txt").write_text("hello\n")

        tools = RepoTools(tmp_path)

        assert tools.read_file("notes.txt") == "hello\n"

    def test_read_file_returns_error_for_missing_file(self, tmp_path: Path) -> None:
        """read_file returns an error string when the file does not exist."""
        tools = RepoTools(tmp_path)

        assert tools.read_file("missing.txt") == "Error: file not found: missing.txt"

    def test_read_file_rejects_dotdot_traversal(self, tmp_path: Path) -> None:
        """read_file refuses a relative path that escapes the repository."""
        repo = tmp_path / "repo"
        repo.mkdir()
        secret = tmp_path / "credentials.txt"
        secret.write_text("super-secret-api-key\n")

        tools = RepoTools(repo)
        result = tools.read_file("../credentials.txt")

        assert "Error: path outside repository" in result
        assert "super-secret-api-key" not in result

    def test_read_file_rejects_absolute_path(self, tmp_path: Path) -> None:
        """read_file refuses an absolute path pointing outside the repository."""
        repo = tmp_path / "repo"
        repo.mkdir()
        secret = tmp_path / "credentials.txt"
        secret.write_text("super-secret-api-key\n")

        tools = RepoTools(repo)
        result = tools.read_file(str(secret))

        assert "Error: path outside repository" in result
        assert "super-secret-api-key" not in result

    def test_list_directory_returns_sorted_entries(self, tmp_path: Path) -> None:
        """list_directory returns a sorted newline-joined list of entry names."""
        src = tmp_path / "src"
        src.mkdir()
        (src / "beta.py").write_text("pass\n")
        (src / "alpha.py").write_text("pass\n")

        tools = RepoTools(tmp_path)

        assert tools.list_directory("src") == "alpha.py\nbeta.py"

    def test_list_directory_returns_error_for_missing_dir(self, tmp_path: Path) -> None:
        """list_directory returns an error string when the directory does not exist."""
        tools = RepoTools(tmp_path)

        assert (
            tools.list_directory("nonexistent")
            == "Error: directory not found: nonexistent"
        )

    def test_list_directory_rejects_dotdot_traversal(self, tmp_path: Path) -> None:
        """list_directory refuses a relative path that escapes the repository."""
        repo = tmp_path / "repo"
        repo.mkdir()

        tools = RepoTools(repo)
        result = tools.list_directory("../..")

        assert "Error: path outside repository" in result

    def test_list_directory_rejects_absolute_path(self, tmp_path: Path) -> None:
        """list_directory refuses an absolute path pointing outside the repository."""
        repo = tmp_path / "repo"
        repo.mkdir()

        tools = RepoTools(repo)
        result = tools.list_directory(str(tmp_path))

        assert "Error: path outside repository" in result

    def test_read_file_reads_nested_file(self, tmp_path: Path) -> None:
        """read_file can read files in subdirectories."""
        subdir = tmp_path / "src" / "pkg"
        subdir.mkdir(parents=True)
        (subdir / "module.py").write_text("x = 1\n")

        tools = RepoTools(tmp_path)

        assert tools.read_file("src/pkg/module.py") == "x = 1\n"

    def test_list_directory_root(self, tmp_path: Path) -> None:
        """list_directory works for the repository root itself."""
        (tmp_path / "a.txt").write_text("")
        (tmp_path / "b.txt").write_text("")

        tools = RepoTools(tmp_path)

        assert tools.list_directory(".") == "a.txt\nb.txt"

    def test_repo_dir_resolved_at_construction(self, tmp_path: Path) -> None:
        """RepoTools resolves symlinks in repo_dir at construction time."""
        real_repo = tmp_path / "real"
        real_repo.mkdir()
        (real_repo / "file.txt").write_text("content\n")
        link = tmp_path / "link"
        link.symlink_to(real_repo)

        tools = RepoTools(link)

        assert tools.read_file("file.txt") == "content\n"
