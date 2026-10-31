.PHONY: lint typecheck test check format cmake-tools

lint:
	uv run ruff check .
	uv run ruff format --check .
	uv run pydoclint src

typecheck:
	uv run pyright

test:
	uv run pytest

check: lint typecheck test

format:
	uv run ruff format .
	uv run ruff check --fix .

cmake-tools:
	grep -v "^#" scripts/cmake-versions.txt | xargs -n1 scripts/install-cmake.sh
