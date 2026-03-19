# maas-code-reviewer: Specification

## Overview

A command-line tool that discovers and reviews merge proposals on
[Launchpad](https://launchpad.net/) and pull requests on
[GitHub](https://github.com/), using a Gemini LLM. It posts its findings as
comments on the merge proposal or pull request. The tool is designed to run in
Jenkins but must also work standalone for local testing.

## Architecture

```
src/maas_code_reviewer/
├── __init__.py
├── cli.py               # argparse CLI entry point
├── launchpad_client.py  # LaunchpadClient protocol + real implementation
├── git.py               # Git checkout / diff operations
├── github_client.py     # GitHubClient + parse_pr_url
├── llm_client.py        # LLMClient protocol + real google.genai implementation
├── models.py            # MergeProposal and Comment dataclasses
├── repo_tools.py        # RepoTools: file-system tools scoped to a repo dir
├── review_schema.py     # JSON review schema, validation, and diff parsing
└── reviewer.py          # LLM-based diff review logic

tests/
├── __init__.py
├── conftest.py           # Shared fixtures
├── factory.py            # Test data factories
├── fake_git.py           # FakeGitClient (uses real temp git repos)
├── fake_github.py        # FakeGitHubClient
├── fake_launchpad.py     # FakeLaunchpadClient
├── fake_launchpadlib.py  # Low-level launchpadlib fake
├── fake_llm.py           # FakeLLMClient / ScriptedResponse
├── test_cli.py
├── test_fakes.py
├── test_git.py
├── test_github_client.py
├── test_launchpad_client.py
├── test_models.py
├── test_repo_tools.py
├── test_review_schema.py
└── test_reviewer.py
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
- **`RepoTools` for file access.** File-system access is scoped to a single
  repo directory via `RepoTools`. This prevents path traversal and is reused
  across `review-mp`, `review-diff`, and `review-pr`.

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

    def get_merge_proposal(self, mp_url: str) -> MergeProposal: ...

    def get_comments(self, mp: MergeProposal) -> list[Comment]: ...

    def post_comment(self, mp: MergeProposal, content: str, subject: str) -> None: ...

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

### GitHubClient

```python
class GitHubClient:
    def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str: ...

    def get_pr_description(self, owner: str, repo: str, pr_number: int) -> str | None: ...

    def post_review(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        comments: list[dict],
    ) -> None: ...
```

`post_review()` submits a pull request review. Each entry in `comments` is a
dict with keys `path` (file path), `line` (new-file line number), and `body`
(comment text).

A free function `parse_pr_url(url: str) -> tuple[str, str, int]` extracts
`(owner, repo, pr_number)` from a GitHub PR URL of the form
`https://github.com/owner/repo/pull/42`.

### LLMClient

```python
class LLMClient(Protocol):
    def review(self, prompt: str, tools: list[Tool]) -> str: ...
```

Where `Tool` is a callable the LLM can invoke (e.g. `read_file`). The real
implementation uses `google.genai`; the fake returns canned responses.

### RepoTools

`RepoTools` provides file-system access scoped to a single repository
directory. All paths are resolved and checked against `repo_dir` before any
operation is performed, preventing path traversal outside the repository tree.

```python
class RepoTools:
    def __init__(self, repo_dir: Path) -> None: ...

    def read_file(self, path: str) -> str: ...

    def list_directory(self, path: str) -> str: ...
```

`read_file` returns the file contents as a string, or an error message string
if the path is outside the repo or the file does not exist.

`list_directory` returns directory entries as a newline-separated string, or
an error message string if the path is outside the repo or is not a directory.

## CLI Commands

### `maas-code-reviewer list-lp-mps`

```
maas-code-reviewer list-lp-mps [--launchpad-credentials FILE] [--status STATUS] PROJECT
```

Lists merge proposals for `PROJECT` filtered by `STATUS`. For each proposal,
prints the URL, status, and the timestamp of the last comment posted by this
tool (or `never`).

| Argument | Description |
|---|---|
| `PROJECT` | Launchpad project name. |
| `--status STATUS` | Filter by MP status (default: `Needs review`). |
| `--launchpad-credentials FILE` | Path to Launchpad credentials file. |

### `maas-code-reviewer review-mp`

```
maas-code-reviewer review-mp [--launchpad-credentials FILE] -g KEY_FILE [--model MODEL] [--dry-run] MP_URL
```

Reviews a single Launchpad merge proposal:

1. Fetch MP metadata and comments from Launchpad.
2. Check whether this tool has already reviewed the MP. If so, skip (exit 0).
3. Clone the target repository, then merge the source branch into it.
4. Generate the diff.
5. Send the diff to the LLM for review, providing tools so it can read
   full files for context.
6. Post the LLM's review as a comment on the MP (unless `--dry-run`).

| Argument | Description |
|---|---|
| `MP_URL` | URL of the merge proposal to review. |
| `-g`, `--gemini-api-key-file` | **(required)** Path to file containing the Gemini API key. |
| `--model MODEL` | Gemini model to use (default: `gemini-3-flash-preview`). |
| `--dry-run` | Print the review to stdout instead of posting it as a comment. |
| `--launchpad-credentials FILE` | Path to Launchpad credentials file. |

### `maas-code-reviewer review-diff`

```
maas-code-reviewer review-diff -g KEY_FILE [--model MODEL] [--repo-dir DIR] [--json-output FILE] DIFF_FILE
```

Reviews a unified diff read from a file (or stdin when `DIFF_FILE` is `-`).

| Argument | Description |
|---|---|
| `DIFF_FILE` | Path to a unified diff file, or `-` to read from stdin. |
| `-g`, `--gemini-api-key-file` | **(required)** Path to file containing the Gemini API key. |
| `--model MODEL` | Gemini model to use (default: `gemini-3-flash-preview`). |
| `--repo-dir DIR` | Path to the local git repository (default: current working directory). Used for `read_file` and `list_directory` tool calls. |
| `--json-output FILE` | Write structured JSON review output to `FILE` instead of plain text to stdout. |

When `--json-output` is not provided, the plain-text review is printed to
stdout.

When `--json-output` is provided, the LLM is instructed to produce structured
JSON output (see [JSON Review Schema](#json-review-schema) below) and the
result is written to the specified file.

### `maas-code-reviewer review-pr`

```
maas-code-reviewer review-pr -g KEY_FILE [--github-token TOKEN] [--model MODEL] [--repo-dir DIR] [--dry-run] PR_URL
```

Reviews a GitHub pull request:

1. Parse the PR URL to extract owner, repo, and PR number.
2. Resolve the GitHub token (from `--github-token` or the `GITHUB_TOKEN`
   environment variable). Error if neither is set.
3. Fetch the diff and description from GitHub.
4. Create `RepoTools` pointed at `--repo-dir` (default: cwd).
5. Call the LLM to produce a structured JSON review.
6. Post the review as a GitHub pull request review (or print on `--dry-run`).

| Argument | Description |
|---|---|
| `PR_URL` | Full GitHub PR URL, e.g. `https://github.com/owner/repo/pull/42`. |
| `-g`, `--gemini-api-key-file` | **(required)** Path to file containing the Gemini API key. |
| `--github-token TOKEN` | GitHub personal access token. Falls back to `GITHUB_TOKEN` env var. |
| `--model MODEL` | Gemini model to use (default: `gemini-3-flash-preview`). |
| `--repo-dir DIR` | Path to a local checkout of the repository (default: current working directory). Used for `read_file` and `list_directory` tool calls. The caller is responsible for having the repo checked out already. |
| `--dry-run` | Print the review JSON to stdout instead of posting it. |

## JSON Review Schema

When `--json-output` is used with `review-diff`, or when `review-pr` posts a
review, the LLM is instructed to produce output in the following JSON format:

```json
{
  "general_comment": "Overall review summary...",
  "inline_comments": {
    "src/foo.py": {
      "42": "This variable is unused.",
      "108": "Consider using a context manager here."
    },
    "src/bar.py": {
      "17": "This condition is always true."
    }
  }
}
```

- `general_comment` — A string containing the overall review. Required.
- `inline_comments` — A JSON object mapping file paths to line-comment maps.
  Required (use `{}` if there are no inline comments).
  - Each key is a file path that must appear in the diff.
  - Each value is a JSON object mapping line number strings to comment strings.
  - Line numbers must correspond to new-file line numbers that appear in the
    diff for that file.

The `validate_review_json(data, diff_text)` function in `review_schema.py`
validates a candidate JSON object against both the schema and the actual diff,
returning a list of error strings (empty means valid). This function is also
exposed to the LLM as a `validate_review` tool so it can self-check its output
before finalising.

## Duplicate Review Prevention (Launchpad)

A merge proposal is considered "already reviewed" if:

1. The tool finds a comment authored by its own bot user (`get_bot_username()`).
2. That comment's body starts with the marker prefix: `[maas-code-reviewer review]`.

## LLM Review Details

### Tools Provided to the LLM

All review commands provide the following tools to the LLM:

- `read_file(path: str) -> str` — Read a file from the working tree (scoped to
  `--repo-dir`). Returns an error message string if the path is outside the
  repo or the file does not exist.
- `list_directory(path: str) -> str` — List directory contents (scoped to
  `--repo-dir`). Returns a newline-separated list of entry names, or an error
  message string if the path is invalid.

The structured review commands (`review-diff --json-output`, `review-pr`)
additionally provide:

- `validate_review(json_text: str) -> str` — Validate a JSON review object
  against the schema and the diff. Returns an empty string if valid, or a
  newline-separated list of errors if invalid. The LLM is instructed to call
  this tool before finalising its output and to fix any reported errors.

### Context Size Management

- The diff is truncated to a configurable maximum (default 30,000 characters).
  If truncated, a note is appended telling the LLM it is seeing a partial diff.
- The total number of tool call rounds the LLM can make is capped (default 20,
  enforced in `GeminiClient`).

### Review Comment Format (Launchpad)

Comments posted to Launchpad merge proposals are prefixed with the review
marker so duplicate detection works:

```
[maas-code-reviewer review]

<LLM review content here>
```

## Dependencies

```toml
dependencies = [
    "google-genai",
    "keyring",
    "launchpadlib",
    "PyGithub",
]

[dependency-groups]
dev = [
    "pytest",
    "pytest-cov",
    "ruff",
    "ty",
]
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `LP_CREDENTIALS_FILE` | No | Path to launchpadlib credentials file. Overridden by `--launchpad-credentials`. |
| `GITHUB_TOKEN` | No | GitHub personal access token. Overridden by `--github-token`. Used by `review-pr`. |
| `GEMINI_API_KEY` | No | Gemini API key. Used internally by `GeminiClient`. Normally supplied via `-g`/`--gemini-api-key-file` instead. |