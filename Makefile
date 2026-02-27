.PHONY: build lint typecheck format test check clean

build:
	uv build

lint:
	uv run ruff check src/

typecheck:
	uv run ty check src/

format:
	uv run ruff format src/

test:
	uv run pytest

check: lint typecheck test

clean:
	rm -rf dist/ .venv/
