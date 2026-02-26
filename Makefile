.PHONY: build lint format clean

build:
	uv build

lint:
	uv run ruff check src/

format:
	uv run ruff format src/

clean:
	rm -rf dist/ .venv/
