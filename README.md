# maas-code-reviewer

An LLM-powered code reviewer agent for [GitHub](https://github.com/) pull
requests and [Launchpad](https://launchpad.net/) merge proposals. It can also be
used to review changes locally before submitting.

Its purpose is to help a human reviewer by taking care of the initial review quickly.
The person proposing the change can then make any changes they feel are necessary
before a human reviewer picks up the PR/MP.

It uses a Gemini LLM to review diffs and post findings as comments on the merge proposal or pull request. 

It's built and maintained by the [MAAS](https://maas.io/) team at
[Canonical](https://canonical.com/). The tool's review defaults reflect what
the MAAS team considers sane behaviour for their projects — but there is
nothing MAAS-specific in how it works, and anyone is welcome to use it on
their own repositories.

## How to use

The primary use case for the code reviewer is on GitHub, where it can
automatically post reviews on new PRs. But it can also be used with
Launchpad MPs or even to review the changes locally. The latter is mostly used
to test out changes to the code reviewer itself.

### Github

Copy the same workflow this project uses: [.github/workflows/review-pr.yml](.github/workflows/review-pr.yml) 

It works as it is. The only thing you might change is `env.MODEL`, which controls
which Gemini model is being used to do the code review.

You also need to create a secret in the repo called `GEMINI_API_KEY` which
contains the API key that you can get from https://aistudio.google.com.

### Launchpad

There's currently no automation to run code reviews automatically on new merge proposals.
At the moment you have to set up your own automation and then use the `review-mp` command.

### Locally

To use it locally, generate the diff that you want reviewed and use the `review-diff`
command. For example

  `git show HEAD | maas-code-reviewer review-diff -`

## Installation

Requires uv and Python ≥ 3.12.

```sh
git clone https://github.com/canonical/maas-code-reviewer
cd maas-code-reviewer
uv sync
```

You can now run it using uv:

```sh
uv run maas-code-reviewer
```

## Configuration

### Gemini API Key

All review commands require a Gemini API key. Provide it in one of two ways:

| Method | Details |
|---|---|
| `-g`, `--gemini-api-key-file FILE` | Path to a file containing the key. |
| `GEMINI_API_KEY` | Set this environment variable to the key. Overridden by `--gemini-api-key-file`. |

```sh
maas-code-reviewer review-mp -g /path/to/gemini-api-key MP_URL
```

### GitHub Token

The `review-pr` command requires a GitHub personal access token. Provide it
in one of two ways:

| Method | Details |
|---|---|
| `--github-token TOKEN` | Pass the token directly on the command line. |
| `GITHUB_TOKEN` | Set this environment variable. Overridden by `--github-token`. |


### Launchpad Credentials

`maas-code-reviewer` uses
[launchpadlib](https://help.launchpad.net/API/launchpadlib) to authenticate
with Launchpad. You can provide credentials in two ways:

| Method | Details |
|---|---|
| `--launchpad-credentials FILE` | Pass a credentials file directly on the command line. |
| `LP_CREDENTIALS_FILE` | Set this environment variable to the path of your credentials file. Overridden by `--launchpad-credentials`. |

If neither is provided, `launchpadlib` will use its default OAuth flow
(opening a browser for authorization on first use).


## Command reference

### `list-lp-mps`

List merge proposals for a Launchpad project, filtered by status.

```sh
maas-code-reviewer list-lp-mps [--launchpad-credentials FILE] [--status STATUS] PROJECT
```

| Argument | Description |
|---|---|
| `PROJECT` | Launchpad project name. |
| `--status STATUS` | Filter by merge proposal status (default: `Needs review`). |
| `--launchpad-credentials FILE` | Path to Launchpad credentials file. |

For each proposal, prints the URL, status, and the timestamp of the last
review posted by this tool (or `never`).

**Example:**

```sh
maas-code-reviewer list-lp-mps --status "Needs review" maas
```

### `review-mp`

Review a single Launchpad merge proposal using Gemini.

```sh
maas-code-reviewer review-mp [--launchpad-credentials FILE] -g KEY_FILE [--model MODEL] [--dry-run] [--metrics FILE] MP_URL
```

| Argument | Description |
|---|---|
| `MP_URL` | URL of the merge proposal to review. |
| `-g`, `--gemini-api-key-file` | Path to file containing the Gemini API key. Falls back to `GEMINI_API_KEY` env var. |
| `--model MODEL` | Gemini model to use (default: `gemini-3-flash-preview`). |
| `--dry-run` | Print the review to stdout instead of posting it as a comment. |
| `--metrics FILE` | Write review metrics as JSON to `FILE` (see [Review Metrics](#review-metrics)). |
| `--launchpad-credentials FILE` | Path to Launchpad credentials file. |

The tool will:

1. Fetch the merge proposal metadata and comments from Launchpad.
2. Skip if a review has already been posted by this tool.
3. Clone the target repository and merge the source branch.
4. Generate a diff.
5. Send the diff to the LLM for review.
6. Post the review as a comment (unless `--dry-run` is set).

**Example:**

```sh
maas-code-reviewer review-mp -g gemini-api-key --dry-run \
  https://code.launchpad.net/~user/project/+git/repo/+merge/123
```

### `review-diff`

Review a unified diff file and print the result to stdout. The diff can be
read from a file or from stdin (pass `-` as the filename).

```sh
maas-code-reviewer review-diff -g KEY_FILE [--model MODEL] [--repo-dir DIR] [--json-output FILE] [--metrics FILE] DIFF_FILE
```

| Argument | Description |
|---|---|
| `DIFF_FILE` | Path to a unified diff file, or `-` to read from stdin. |
| `-g`, `--gemini-api-key-file` | Path to file containing the Gemini API key. Falls back to `GEMINI_API_KEY` env var. |
| `--model MODEL` | Gemini model to use (default: `gemini-3-flash-preview`). |
| `--repo-dir DIR` | Path to the local git repository (default: current working directory). Used for `read_file` and `list_directory` tool calls. |
| `--json-output FILE` | Write structured JSON review output to `FILE` instead of plain text to stdout. |
| `--metrics FILE` | Write review metrics as JSON to `FILE` (see [Review Metrics](#review-metrics)). |

When `--json-output` is provided, the LLM produces structured output with a
general comment and inline comments keyed by file path and line number (see
[JSON Review Format](#json-review-format) below).

**Examples:**

```sh
# Review a diff file, print plain text to stdout
maas-code-reviewer review-diff -g gemini-api-key changes.diff

# Review from stdin
git diff HEAD~1 | maas-code-reviewer review-diff -g gemini-api-key -

# Produce structured JSON output
maas-code-reviewer review-diff -g gemini-api-key --json-output review.json changes.diff
```

### `review-pr`

Review a GitHub pull request using Gemini and post the review via the GitHub
API.

```sh
maas-code-reviewer review-pr -g KEY_FILE [--github-token TOKEN] [--model MODEL] [--repo-dir DIR] [--dry-run] [--metrics FILE] PR_URL
```

| Argument | Description |
|---|---|
| `PR_URL` | Full GitHub PR URL, e.g. `https://github.com/owner/repo/pull/42`. |
| `-g`, `--gemini-api-key-file` | Path to file containing the Gemini API key. Falls back to `GEMINI_API_KEY` env var. |
| `--github-token TOKEN` | GitHub personal access token. Falls back to `GITHUB_TOKEN` env var. |
| `--model MODEL` | Gemini model to use (default: `gemini-3-flash-preview`). |
| `--repo-dir DIR` | Path to a local checkout of the repository (default: current working directory). Used for `read_file` and `list_directory` tool calls. The caller is responsible for having the repo checked out already. |
| `--dry-run` | Print the review JSON to stdout instead of posting it. |
| `--metrics FILE` | Write review metrics as JSON to `FILE` (see [Review Metrics](#review-metrics)). |

The tool will:

1. Parse the PR URL to extract the owner, repository, and PR number.
2. Fetch the diff and description from GitHub.
3. Send the diff to the LLM for a structured review.
4. Post the review as a GitHub pull request review (unless `--dry-run`).

**Examples:**

```sh
# Review a PR and post the result
maas-code-reviewer review-pr -g gemini-api-key \
  https://github.com/canonical/maas/pull/42

# Dry run — print the JSON review to stdout
maas-code-reviewer review-pr -g gemini-api-key --dry-run \
  https://github.com/canonical/maas/pull/42

# Use a local checkout for extra context
maas-code-reviewer review-pr -g gemini-api-key --repo-dir /path/to/maas \
  https://github.com/canonical/maas/pull/42
```

## JSON Review Format

When `review-diff --json-output` is used, or when `review-pr` posts a review,
the LLM produces structured output with this schema:

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

- `general_comment` — An overall review summary string.
- `inline_comments` — A map from file path to a map from line number string
  to comment string. Only file paths and line numbers that appear in the diff
  are valid. Use `{}` for no inline comments.

## Review Metrics

All three review commands (`review-mp`, `review-diff`, `review-pr`) accept an
optional `--metrics FILE` flag. When provided, a JSON file is written after
the review completes containing usage and context metrics:

```json
{
  "model_name": "gemini-3-flash-preview",
  "tokens_thinking": 1234,
  "tokens_input": 5678,
  "tokens_output": 910,
  "files_read": 3,
  "agents_md_read": true,
  "diff_lines": 42,
  "diff_size_bytes": 2048
}
```

| Field | Description |
|---|---|
| `model_name` | The Gemini model used for the review. |
| `tokens_thinking` | Number of internal thinking tokens used by the model. |
| `tokens_input` | Number of input (prompt) tokens. |
| `tokens_output` | Number of output (completion) tokens. |
| `files_read` | Number of files read via the `read_file` tool during the review. |
| `agents_md_read` | Whether an `AGENTS.md` file was read during the review. |
| `diff_lines` | Number of lines in the original diff (before any truncation). |
| `diff_size_bytes` | Size of the original diff in bytes (UTF-8 encoded). |

The metrics file is written after the review completes. If the review is
skipped (e.g. an MP was already reviewed), no metrics file is written.

**Example:**

```sh
maas-code-reviewer review-diff -g gemini-api-key --metrics metrics.json changes.diff
cat metrics.json
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GITHUB_TOKEN` | No | GitHub personal access token. Overridden by `--github-token`. Used by `review-pr`. |

## Development

Install development dependencies:

```sh
uv sync
```

Run all checks (lint, typecheck, tests):

```sh
make check
```

Individual targets:

```sh
make lint        # ruff check
make typecheck   # ty check
make format      # ruff format
make test        # pytest
make coverage    # pytest with 100% coverage enforcement
```

## License

Copyright MAAS Developers.
