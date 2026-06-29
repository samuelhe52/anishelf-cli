PYTHON := uv run

.PHONY: sync test lint format typecheck check

sync:
	uv sync

test:
	$(PYTHON) pytest

lint:
	$(PYTHON) ruff check .

format:
	$(PYTHON) ruff format .

typecheck:
	$(PYTHON) mypy src

check: lint typecheck test

