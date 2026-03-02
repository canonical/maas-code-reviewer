# lp-ci-tools: Merge Proposal Review Tool — Specification

## Overview

A command-line tool that discovers new merge proposals on launchpad.net for a
given project and reviews them using a Gemini LLM. It posts its findings as
comments on the merge proposal. The tool is designed to run in Jenkins but must
also work standalone for local testing.

## Existing Project State

- Python project managed with `uv` and `hatchling`.
- Entry point: `lp-ci-tools` → `lp_ci_tools.main:main`.
- `src/lp_ci_tools/main.py` contains legacy code that interacts with the
  Launchpad API. This code is **reference only** — it shows how to use
  `launchpadlib` to list merge proposals, read comments, and post comments.
  It will be deleted once the new implementation is complete.
- Dependencies already declared: `launchpadlib`, `keyring`, `pyyaml`.

## Architecture

```
src/lp_ci_tools/
├── __init__.py
├── cli.py               # argparse CLI entry point
├── launchpad_client.py   # LaunchpadClient protocol + real implementation
├── git.py                # Git checkout / diff operations
├── reviewer.py           # LLM-based diff review logic
├── llm_client.py         # LLM client protocol + real google.genai implementation
└── main.py               # Legacy code (deleted in final chunk)

tests/
├── __init__.py
├── conftest.py           # Shared fixtures
├── fake_launchpad.py     # FakeLaunchpadClient
├── fake_llm.py           # FakeLLMClient
├── fake_git.py           # FakeGitClient
├── test_cli.py
├── test_launchpad_client.py
├── test_git.py
├── test_reviewer.py
└── test_llm_client.py
```

### Key Design Decisions

- **Protocols, not base classes.** Each external dependency (Launchpad, Git,
  LLM) is accessed through a `typing.Protocol`. Production code depends on
  the protocol; tests inject fakes that implement the same protocol.
- **No mocking.** Fakes maintain internal state (e.g. `FakeLaunchpadClient`
  stores merge proposals and comments in plain dicts/lists) so tests exercise
  real logic paths.
- **Thin CLI layer.** `cli.py` only parses arguments and wires dependencies.
  All logic lives in modules that receive their dependencies as arguments.
- **100% test coverage target.** Every module except `main.py` (legacy) must
  have full test coverage.

## Data Models

These are plain dataclasses used throughout the codebase. They decouple the
internal logic from the launchpadlib object model.

```python
@dataclass(frozen=True)
class MergeProposal:
    url: str                    # web_link, e.g. https://code.launchpad.net/~user/project/+git/repo/+merge/123456
    source_git_repository: str  # unique name of source repo
    source_git_path: str        # refs/heads/branch-name
    target_git_repository: str  # unique name of target repo
    target_git_path: str        # refs/heads/main
    status: str                 # "Needs review", "Approved", etc.
    commit_message: str | None
    description: str | None

@dataclass(frozen=True)
class Comment:
    author: str       # username or display name
    body: str
    date: datetime
```

## Protocols

### LaunchpadClient

```python
class LaunchpadClient(Protocol):
    def get_merge_proposals(
        self, project: str, status: str
    ) -> list[MergeProposal]: ...

    def get_comments(self, mp_url: str) -> list[Comment]: ...

    def post_comment(self, mp_url: str, content: str, subject: str) -> None: ...

    def get_bot_username(self) -> str: ...
```

`get_bot_username()` returns the identity of the logged-in user so the tool
can identify its own previous comments.

### GitClient

```python
class GitClient(Protocol):
    def clone(self, repo_url: str, dest: Path, branch: str) -> None: ...

    def diff(self, repo_dir: Path, base_ref: str, head_ref: str) -> str: ...

    def merge_into(
        self, repo_dir: Path, source_url: str, source_branch: str
    ) -> None: ...

    def read_file(self, repo_dir: Path, path: str) -> str | None: ...

    def list_changed_files(
        self, repo_dir: Path, base_ref: str, head_ref: str
    ) -> list[str]: ...
```

### LLMClient

```python
class LLMClient(Protocol):
    def review(self, prompt: str, tools: list[Tool]) -> str: ...
```

Where `Tool` is a callable the LLM can invoke (e.g. `read_file`). The real
implementation uses `google.genai`; the fake returns canned responses.

## CLI Commands

### `lp-ci-tools list-merge-proposals`

```
lp-ci-tools list-merge-proposals [--launchpad-credentials FILE] --status STATUS PROJECT
```

Lists merge proposals for PROJECT filtered by STATUS. For each proposal,
prints the URL, status, and the timestamp of the last comment posted by
this tool (or "never").

### `lp-ci-tools review`

```
lp-ci-tools review [--launchpad-credentials FILE] -g KEY_FILE [--dry-run] MP_URL
```

Reviews a single merge proposal:

1. Fetch MP metadata and comments from Launchpad.
2. Check whether this tool has already reviewed the MP. If so, skip (exit 0).
3. Clone the target repository, then merge the source branch into it.
4. Generate the diff.
5. Send the diff to the LLM for review, providing tools so it can read
   full files for context and look for an `AGENTS.md` file.
6. Post the LLM's review as a comment on the MP (unless `--dry-run`).

### `lp-ci-tools review-new`

```
lp-ci-tools review-new [--launchpad-credentials FILE] -g KEY_FILE [--dry-run] --status STATUS PROJECT
```

Combines list + review: finds all merge proposals matching STATUS for
PROJECT, filters out already-reviewed ones, and reviews each remaining one.
This is the command Jenkins will call.

## Duplicate Review Prevention

A merge proposal is considered "already reviewed" if:

1. The tool finds a comment authored by its own bot user
   (`get_bot_username()`).
2. That comment's body starts with a known marker prefix:
   `[lp-ci-tools review]`.

This is deliberately simple. Future work can extend it to track the source
commit SHA that was reviewed, enabling re-review when new commits are pushed.

## LLM Review Details

### Prompt Structure

The reviewer builds a prompt containing:

1. A system instruction explaining the role (code reviewer) and output format.
2. The diff.
3. The merge proposal description / commit message (if available).
4. Instructions to use the provided tools for additional context when needed.

### Tools Provided to the LLM

- `read_file(path: str) -> str` — Read a file from the merged working tree.
  The reviewer imposes a size limit (e.g. 100 KB) and truncates with a
  message if exceeded.
- `list_directory(path: str) -> list[str]` — List directory contents.

### Context Size Management

- The diff is truncated to a configurable maximum (default 30,000 chars).
  If truncated, a note is appended telling the LLM it's seeing a partial diff.
- File reads requested by the LLM are individually capped.
- The total number of tool calls the LLM can make is capped (default 20).

## Review Comment Format

```
[lp-ci-tools review]

<LLM review content here>
```

The `[lp-ci-tools review]` prefix is mandatory — it's how duplicate detection
works.

---

## Implementation Plan

Each chunk is a self-contained unit of work. Tests are written first (TDD).
Each chunk should produce a working, testable increment. Target ≤ 1000 lines
of diff per chunk.

---

### Chunk 1: Project Setup, Data Models, and Fake Launchpad Client

**Goal:** Establish the test infrastructure, define data models, define the
`LaunchpadClient` protocol, and implement `FakeLaunchpadClient`.

**Files to create:**
- `tests/__init__.py`
- `tests/conftest.py` — shared fixtures (empty initially)
- `src/lp_ci_tools/models.py` — `MergeProposal` and `Comment` dataclasses
- `src/lp_ci_tools/launchpad_client.py` — `LaunchpadClient` protocol
- `tests/fake_launchpad.py` — `FakeLaunchpadClient` with internal state
- `tests/test_launchpad_client.py` — tests that exercise the fake to verify
  the contract (add proposals, list them, add/read comments, filter by status)

**Files to modify:**
- `pyproject.toml` — add `pytest` and `pytest-cov` to dev dependencies

**Acceptance criteria:**
- `uv run pytest` passes.
- `FakeLaunchpadClient` can:
  - Store merge proposals (added via helper methods on the fake).
  - Return them filtered by project and status.
  - Store and return comments per MP.
  - Track a bot username.
- Data models are frozen dataclasses.

---

### Chunk 2: `list-merge-proposals` Command

**Goal:** Implement the CLI command that lists merge proposals and shows the
last review timestamp.

**Files to create:**
- `src/lp_ci_tools/cli.py` — argument parsing and `list_merge_proposals`
  handler
- `tests/test_cli.py` — tests for `list-merge-proposals`, using
  `FakeLaunchpadClient`

**Files to modify:**
- `pyproject.toml` — update entry point to `lp_ci_tools.cli:main`

**Details:**
- `list_merge_proposals(client: LaunchpadClient, project: str, status: str)`
  is a pure function that returns structured data. The CLI layer formats it.
- The handler scans comments for each MP to find the latest one by the bot
  user that starts with `[lp-ci-tools review]`.

**Acceptance criteria:**
- `uv run pytest` passes with full coverage of new code.
- The command outputs the expected format given fake data with various
  combinations of reviewed / not-reviewed proposals.

---

### Chunk 3: Git Client Protocol, Fake, and Diff Generation

**Goal:** Implement the `GitClient` protocol, `FakeGitClient`, and the real
`GitClient` (wrapping subprocess calls to git).

**Files to create:**
- `src/lp_ci_tools/git.py` — `GitClient` protocol + `RealGitClient`
- `tests/fake_git.py` — `FakeGitClient` that operates on a real temp
  directory with actual git commands (creating small test repos)
- `tests/test_git.py` — tests for diff generation, merge, file reading

**Details:**
- `FakeGitClient` is *not* a pure in-memory fake. Instead, the test helper
  creates real git repos in a temp directory with known commits. This tests
  the real git interaction without needing network access.
- `RealGitClient` wraps subprocess calls to `git clone`, `git merge`,
  `git diff`, etc.
- `read_file` reads a file from the working tree relative to the repo root.
  Returns `None` if the file doesn't exist.
- `list_changed_files` returns the list of files changed between two refs.

**Acceptance criteria:**
- `uv run pytest` passes with full coverage of new code.
- Tests create temp git repos, make commits, and verify diffs/merges.

---

### Chunk 4: LLM Client Protocol, Fake, and Reviewer Logic

**Goal:** Implement the reviewer module that takes a diff and produces a
review using an LLM, with tool support for reading files.

**Files to create:**
- `src/lp_ci_tools/llm_client.py` — `LLMClient` protocol + real
  `GeminiClient` (using `google.genai`)
- `tests/fake_llm.py` — `FakeLLMClient` that returns canned/scripted
  responses and records tool calls
- `src/lp_ci_tools/reviewer.py` — `review_diff()` function that orchestrates
  prompt construction, tool binding, and LLM invocation
- `tests/test_reviewer.py` — tests for review logic

**Files to modify:**
- `pyproject.toml` — add `google-genai` dependency

**Details for `reviewer.py`:**
- `review_diff(llm: LLMClient, diff: str, description: str | None,
  read_file: Callable, list_directory: Callable, max_diff_chars: int = 30000)
  -> str`
- Truncates the diff if it exceeds `max_diff_chars`.
- Constructs the prompt with the system instruction, diff, and description.
- Provides `read_file` and `list_directory` as tools.
- Returns the LLM's textual review.

**Details for `FakeLLMClient`:**
- Accepts a list of scripted responses at construction time.
- Each call to `review()` pops the next response.
- Records the prompts and tool definitions it received, so tests can assert
  on them.
- Can be configured to call tools (e.g. "call read_file('AGENTS.md') then
  return review text").

**Details for `GeminiClient`:**
- Uses `google.genai` to create a chat session.
- Translates the tool definitions into the google.genai function-calling
  format.
- Caps the number of tool-call rounds (default 20).
- Reads the API key from the `GEMINI_API_KEY` environment variable.

**Acceptance criteria:**
- `uv run pytest` passes with full coverage of new code (excluding the real
  `GeminiClient` which depends on an API key).
- Tests verify: prompt construction, diff truncation, tool invocation, and
  response extraction.

---

### Chunk 5: `review` Command — Wire Everything Together

**Goal:** Implement the `review` CLI command that reviews a single merge
proposal end to end.

**Files to modify:**
- `src/lp_ci_tools/cli.py` — add `review` subcommand and handler
- `tests/test_cli.py` — end-to-end tests for the `review` command

**Details:**
- The `review` handler:
  1. Fetches the MP from Launchpad.
  2. Checks for an existing review comment. If found, prints a message and
     exits.
  3. Clones the target repo into a temp directory.
  4. Merges the source branch.
  5. Generates the diff.
  6. Calls `review_diff()` with tool callbacks bound to the working tree.
  7. Posts the review as a comment (unless `--dry-run`, in which case it
     prints to stdout).
- The comment body is prefixed with `[lp-ci-tools review]\n\n`.

**Acceptance criteria:**
- `uv run pytest` passes with full coverage.
- An end-to-end test using all fakes verifies the full flow: MP exists →
  not yet reviewed → clone → merge → diff → LLM review → comment posted.
- A test verifies that an already-reviewed MP is skipped.
- A test verifies `--dry-run` prints to stdout instead of posting.

---

### Chunk 6: `review-new` Command and Jenkins Integration

**Goal:** Implement the `review-new` command that combines listing and
reviewing, suitable for Jenkins.

**Files to modify:**
- `src/lp_ci_tools/cli.py` — add `review-new` subcommand
- `tests/test_cli.py` — tests for `review-new`

**Details:**
- Iterates over all merge proposals matching the given status.
- Skips already-reviewed ones.
- Reviews each remaining one.
- Prints a summary at the end (number reviewed, number skipped).
- Errors reviewing one MP do not prevent reviewing the next (catch and log).

**Acceptance criteria:**
- `uv run pytest` passes with full coverage.
- Test with multiple MPs: some reviewed, some not. Verify only the
  unreviewed ones get reviewed.
- Test that an error during one review doesn't stop the others.

---

### Chunk 7: Cleanup and Documentation

**Goal:** Remove legacy code, finalize documentation, and polish.

**Files to delete:**
- The body of `src/lp_ci_tools/main.py` — replace with a stub that imports
  and calls `cli.main()` for backwards compatibility, or delete entirely.

**Files to modify:**
- `README.md` — document installation, configuration, and usage.
- `Makefile` — add `test` and `coverage` targets; update `lint`/`format` to
  include `tests/`.
- `pyproject.toml` — clean up any unused dependencies (e.g. `pyyaml` if not
  needed).

**Files to create:**
- `src/lp_ci_tools/real_launchpad_client.py` — the real `LaunchpadClient`
  implementation using `launchpadlib`. This is separated from the protocol
  file to keep the protocol dependency-free.

**Acceptance criteria:**
- `uv run pytest --cov --cov-fail-under=100` passes.
- `lp-ci-tools --help` shows the new commands.
- `README.md` documents all commands and environment variables.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `LP_CREDENTIALS_FILE` | No | Path to launchpadlib credentials. Overridden by `--launchpad-credentials`. |

## Dependencies (Final)

```toml
dependencies = [
    "google-genai",
    "keyring",
    "launchpadlib",
]

[dependency-groups]
dev = [
    "pytest",
    "pytest-cov",
    "ruff",
]
```

## Future Work (Out of Scope)

These are **not** part of the current implementation plan but inform the
architecture:

- **Incremental reviews:** Track the source commit SHA in the review comment.
  Re-review when new commits are pushed.
- **Conversational reviews:** After the initial review, wait for the MP author
  to reply, then perform a follow-up review incorporating their feedback.
- **Configurable review instructions:** Read project-specific review
  guidelines from a config file in the repo (e.g. `.lp-ci-tools.yml`).
- **Multiple LLM backends:** Support other LLM providers beyond Gemini.