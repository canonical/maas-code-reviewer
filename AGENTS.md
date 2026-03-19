# AGENTS.md

## Project Overview

`maas-code-reviewer` is a Python CLI tool that reviews [Launchpad](https://launchpad.net/) merge proposals and [GitHub](https://github.com/) pull requests using a Gemini LLM. See `SPEC.md` for the full specification.

## Project Structure

- `src/maas_code_reviewer/` — main package
  - `cli.py` — argparse CLI entry point; wires dependencies and dispatches subcommands
  - `launchpad_client.py` — `LaunchpadClient` protocol + real implementation
  - `git.py` — `GitClient` protocol + real implementation (subprocess-based)
  - `github_client.py` — `GitHubClient` class and `parse_pr_url()` free function
  - `llm_client.py` — `LLMClient` protocol + real `GeminiClient` (google-genai)
  - `models.py` — `MergeProposal` and `Comment` dataclasses
  - `repo_tools.py` — `RepoTools`: file-system access scoped to a repo directory
  - `review_schema.py` — JSON review schema, `validate_review_json()`, `parse_diff_files_and_lines()`
  - `reviewer.py` — `review_diff()` and `review_diff_structured()` orchestration logic
- `tests/` — tests and fakes
  - `factory.py` — test data factories
  - `fake_git.py` — `FakeGitClient` (uses real temp git repos)
  - `fake_github.py` — `FakeGitHubClient`
  - `fake_launchpad.py` — `FakeLaunchpadClient`
  - `fake_launchpadlib.py` — low-level launchpadlib fake
  - `fake_llm.py` — `FakeLLMClient` / `ScriptedResponse`
- `pyproject.toml` — project config (hatchling build, uv for deps)
- `Makefile` — lint, typecheck, test, and build targets

## Workflow

After every change, run:

```
make check
```

This runs linting (`ruff`), type checking (`ty`), and tests (`pytest`). All three must pass before a change is considered complete.

## Conventions

- Python ≥ 3.12
- Keep `try`/`except` blocks minimal — only wrap the code that can actually raise the caught exception.
- Catch specific exceptions, never bare `Exception`.
- Avoid regular expressions. Prefer string methods (`.split()`, `.startswith()`, `.endswith()`).
- Avoid mocking. Use fakes (see `tests/fake_launchpad.py`, `tests/fake_github.py`, `tests/fake_llm.py`) — objects with the same API as the real thing but with exposed internal state for test control.
- Follow the protocols defined in `SPEC.md` when adding new clients or services.
- In modules and classes that have both public and private (underscore-prefixed) methods/functions, public ones come first, private ones at the bottom.
- `cli.py` is a thin wiring layer only — all logic lives in dedicated modules.
- All file-system access from the LLM tools goes through `RepoTools`, which prevents path traversal outside the repository directory.