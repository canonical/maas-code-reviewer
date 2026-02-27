# AGENTS.md

## Project Overview

`lp-ci-tools` is a Python CLI tool that interacts with Launchpad for CI tasks such as listing merge proposals and posting automated reviews. See `SPEC.md` for the full specification.

## Project Structure

- `src/lp_ci_tools/` — main package
- `tests/` — tests and fakes
- `pyproject.toml` — project config (hatchling build, uv for deps)
- `Makefile` — lint, typecheck, test, and build targets

## Note on `main.py`

`src/lp_ci_tools/main.py` is **legacy code**. Use it as a reference only — it will eventually be deleted. Do not build on top of it.

## Workflow

After every change, run:

```
make check
```

This runs linting (`ruff`), type checking (`ty`), and tests (`pytest`). All three must pass before a change is considered complete.

## Conventions

- Python ≥ 3.13
- Keep `try`/`except` blocks minimal — only wrap the code that can actually raise the caught exception.
- Catch specific exceptions, never bare `Exception`.
- Avoid regular expressions. Prefer string methods (`.split()`, `.startswith()`, `.endswith()`).
- Avoid mocking. Use fakes (see `tests/fake_launchpad.py`) — objects with the same API as the real thing but with exposed internal state for test control.
- Follow the protocols defined in `SPEC.md` when adding new clients or services.