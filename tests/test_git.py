from __future__ import annotations

from pathlib import Path

from tests.fake_git import FakeGitClient


class TestClone:
    def test_clones_repo(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        origin = tmp_path / "origin"
        client.create_repo(origin)
        client.add_commit(origin, {"README.md": "hello"}, message="initial")

        dest = tmp_path / "clone"
        client.clone(str(origin), dest, "main")

        assert (dest / "README.md").read_text() == "hello"

    def test_clones_specific_branch(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        origin = tmp_path / "origin"
        client.create_repo(origin)
        client.add_commit(origin, {"a.txt": "main content"}, message="on main")
        client.create_branch(origin, "feature")
        client.checkout(origin, "feature")
        client.add_commit(origin, {"b.txt": "feature content"}, message="on feature")

        dest = tmp_path / "clone"
        client.clone(str(origin), dest, "feature")

        assert (dest / "b.txt").read_text() == "feature content"


class TestDiff:
    def test_diff_between_two_commits(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        base_sha = client.add_commit(repo, {"file.txt": "original\n"}, message="base")
        head_sha = client.add_commit(repo, {"file.txt": "modified\n"}, message="change")

        result = client.diff(repo, base_sha, head_sha)

        assert "-original" in result
        assert "+modified" in result

    def test_diff_with_no_changes(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        sha = client.add_commit(repo, {"file.txt": "same\n"}, message="base")

        result = client.diff(repo, sha, sha)

        assert result == ""

    def test_diff_with_new_file(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        base_sha = client.add_commit(repo, {"a.txt": "a\n"}, message="base")
        head_sha = client.add_commit(repo, {"b.txt": "b\n"}, message="add file")

        result = client.diff(repo, base_sha, head_sha)

        assert "b.txt" in result
        assert "+b" in result

    def test_diff_across_branches(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        client.add_commit(repo, {"file.txt": "base\n"}, message="initial")
        client.create_branch(repo, "feature")
        client.checkout(repo, "feature")
        client.add_commit(repo, {"file.txt": "changed\n"}, message="feature work")

        result = client.diff(repo, "main", "feature")

        assert "-base" in result
        assert "+changed" in result


class TestMergeInto:
    def test_merge_brings_in_changes(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        origin = tmp_path / "origin"
        client.create_repo(origin)
        client.add_commit(origin, {"base.txt": "base\n"}, message="initial")
        client.create_branch(origin, "feature")
        client.checkout(origin, "feature")
        client.add_commit(origin, {"new.txt": "feature work\n"}, message="feature")
        client.checkout(origin, "main")

        clone = tmp_path / "clone"
        client.clone(str(origin), clone, "main")

        client.merge_into(clone, str(origin), "feature")

        assert (clone / "new.txt").read_text() == "feature work\n"
        assert (clone / "base.txt").read_text() == "base\n"

    def test_merge_from_separate_repo(self, tmp_path: Path) -> None:
        client = FakeGitClient()

        target_repo = tmp_path / "target"
        client.create_repo(target_repo)
        client.add_commit(target_repo, {"shared.txt": "original\n"}, message="init")

        source_repo = tmp_path / "source"
        client.clone(str(target_repo), source_repo, "main")
        client.add_commit(
            source_repo, {"extra.txt": "from source\n"}, message="source commit"
        )

        client.merge_into(target_repo, str(source_repo), "main")

        assert (target_repo / "extra.txt").read_text() == "from source\n"


class TestReadFile:
    def test_reads_existing_file(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        client.add_commit(repo, {"hello.txt": "world"}, message="init")

        result = client.read_file(repo, "hello.txt")

        assert result == "world"

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        client.add_commit(repo, {"a.txt": "exists"}, message="init")

        result = client.read_file(repo, "nonexistent.txt")

        assert result is None

    def test_reads_file_in_subdirectory(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        client.add_commit(repo, {"sub/dir/file.txt": "nested"}, message="init")

        result = client.read_file(repo, "sub/dir/file.txt")

        assert result == "nested"

    def test_returns_none_for_directory(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        client.add_commit(repo, {"dir/file.txt": "content"}, message="init")

        result = client.read_file(repo, "dir")

        assert result is None


class TestListChangedFiles:
    def test_lists_changed_files(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        base_sha = client.add_commit(repo, {"a.txt": "a\n"}, message="base")
        head_sha = client.add_commit(
            repo,
            {"a.txt": "a modified\n", "b.txt": "b\n"},
            message="changes",
        )

        result = client.list_changed_files(repo, base_sha, head_sha)

        assert sorted(result) == ["a.txt", "b.txt"]

    def test_no_changes_returns_empty_list(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        sha = client.add_commit(repo, {"a.txt": "a\n"}, message="base")

        result = client.list_changed_files(repo, sha, sha)

        assert result == []

    def test_lists_files_in_subdirectories(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        base_sha = client.add_commit(repo, {"x.txt": "x\n"}, message="base")
        head_sha = client.add_commit(
            repo, {"sub/deep/new.txt": "new\n"}, message="add nested"
        )

        result = client.list_changed_files(repo, base_sha, head_sha)

        assert result == ["sub/deep/new.txt"]

    def test_lists_across_branches(self, tmp_path: Path) -> None:
        client = FakeGitClient()
        repo = tmp_path / "repo"
        client.create_repo(repo)
        client.add_commit(repo, {"a.txt": "a\n"}, message="initial")
        client.create_branch(repo, "feature")
        client.checkout(repo, "feature")
        client.add_commit(repo, {"b.txt": "b\n"}, message="feature work")
        client.add_commit(repo, {"c.txt": "c\n"}, message="more feature work")

        result = client.list_changed_files(repo, "main", "feature")

        assert sorted(result) == ["b.txt", "c.txt"]
