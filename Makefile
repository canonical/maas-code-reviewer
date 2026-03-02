.PHONY: build lint typecheck format test coverage check clean

build:
	uv build

lint:
	uv run ruff check src/ tests/

typecheck:
	uv run ty check src/

format:
	uv run ruff format src/ tests/

test:
	uv run pytest

coverage:
	uv run pytest --cov --cov-fail-under=100

check: lint typecheck test

clean:
	rm -rf dist/ .venv/
