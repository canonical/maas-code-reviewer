# lp-ci-tools

A command-line tool that discovers merge proposals on
[Launchpad](https://launchpad.net/) and reviews them using a Gemini LLM. It
posts its findings as comments on the merge proposal. Designed to run in
Jenkins but also works standalone for local testing.

## Installation

Requires Python ≥ 3.13.

```sh
uv sync
```

To build a distributable wheel:

```sh
make build
```

## Configuration

### Launchpad Credentials

`lp-ci-tools` uses [launchpadlib](https://help.launchpad.net/API/launchpadlib)
to authenticate with Launchpad. You can provide credentials in two ways:

| Method | Details |
|---|---|
| `--launchpad-credentials FILE` | Pass a credentials file directly on the command line. |
| `LP_CREDENTIALS_FILE` | Set this environment variable to the path of your credentials file. Overridden by `--launchpad-credentials`. |

If neither is provided, `launchpadlib` will use its default OAuth flow
(opening a browser for authorization on first use).

### Gemini API Key

The `review` and `review-new` commands require a Gemini API key. Provide the
path to a file containing the key with the `-g` / `--gemini-api-key-file`
flag:

```sh
lp-ci-tools review -g /path/to/gemini-api-key MP_URL
```

## Usage

### `list-merge-proposals`

List merge proposals for a Launchpad project, filtered by status.

```sh
lp-ci-tools list-merge-proposals [--launchpad-credentials FILE] [--status STATUS] PROJECT
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
lp-ci-tools list-merge-proposals --status "Needs review" maas
```

### `review`

Review a single merge proposal using Gemini.

```sh
lp-ci-tools review [--launchpad-credentials FILE] -g KEY_FILE [--model MODEL] [--dry-run] MP_URL
```

| Argument | Description |
|---|---|
| `MP_URL` | URL of the merge proposal to review. |
| `-g`, `--gemini-api-key-file` | **(required)** Path to file containing the Gemini API key. |
| `--model MODEL` | Gemini model to use (default: `gemini-3-flash-preview`). |
| `--dry-run` | Print the review to stdout instead of posting it as a comment. |
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
lp-ci-tools review -g gemini-api-key --dry-run \
  https://code.launchpad.net/~user/project/+git/repo/+merge/123
```

### `review-new`

Review all unreviewed merge proposals for a project. This is the command
intended for Jenkins.

```sh
lp-ci-tools review-new [--launchpad-credentials FILE] -g KEY_FILE [--model MODEL] [--dry-run] --status STATUS PROJECT
```

| Argument | Description |
|---|---|
| `PROJECT` | Launchpad project name. |
| `--status STATUS` | Filter by merge proposal status (default: `Needs review`). |
| `-g`, `--gemini-api-key-file` | **(required)** Path to file containing the Gemini API key. |
| `--model MODEL` | Gemini model to use (default: `gemini-3-flash-preview`). |
| `--dry-run` | Print reviews to stdout instead of posting them as comments. |
| `--launchpad-credentials FILE` | Path to Launchpad credentials file. |

The tool iterates over all merge proposals matching the given status, skips
any that have already been reviewed, and reviews the rest. An error reviewing
one proposal does not prevent the others from being reviewed.

**Example:**

```sh
lp-ci-tools review-new -g gemini-api-key --status "Needs review" maas
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `LP_CREDENTIALS_FILE` | No | Path to launchpadlib credentials. Overridden by `--launchpad-credentials`. |

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