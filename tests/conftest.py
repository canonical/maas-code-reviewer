from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _isolate_git_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Prevent git from reading the user's global and system config.

    Without this, tests that create real git repos (via ``FakeGitClient``)
    can pick up settings like ``commit.gpgsign = true`` from
    ``~/.gitconfig``, causing failures when the GPG agent isn't running.

    Setting ``GIT_CONFIG_GLOBAL`` and ``GIT_CONFIG_SYSTEM`` to
    ``/dev/null`` makes git ignore those files entirely.  We also
    inject fallback identity env-vars so that cloned repos (which
    don't have local ``user.name`` / ``user.email`` config) can
    still create commits.
    """
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Test")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "test@test.com")
    monkeypatch.setenv("GIT_COMMITTER_NAME", "Test")
    monkeypatch.setenv("GIT_COMMITTER_EMAIL", "test@test.com")
