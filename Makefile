.PHONY: lint typecheck test check format

lint:
	uv run ruff check .
	uv run ruff format --check .
	uvx pydoclint@0.12.0 src

typecheck:
	uv run pyright

test:
	uv run pytest

check: lint typecheck test

format:
	uv run ruff format .
	uv run ruff check --fix .
