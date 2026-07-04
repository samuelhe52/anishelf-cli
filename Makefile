UV := uv
RUN := $(UV) run

.DEFAULT_GOAL := help

.PHONY: help sync test test-cov lint lint-fix format format-check fix typecheck check clean

help:
	@printf '%s\n' \
		'Available targets:' \
		'  make sync          Install or update project dependencies' \
		'  make test          Run the pytest suite' \
		'  make test-cov      Run pytest with coverage output' \
		'  make lint          Run ruff checks' \
		'  make lint-fix      Apply safe ruff fixes' \
		'  make format        Format the repo with ruff' \
		'  make format-check  Verify formatting without changing files' \
		'  make fix           Run lint fixes and formatting' \
		'  make typecheck     Run mypy against src' \
		'  make check         Run format, lint, typecheck, and tests' \
		'  make clean         Remove local tool caches and build artifacts'

sync:
	$(UV) sync

test:
	$(RUN) pytest

test-cov:
	$(RUN) pytest --cov=anishelf_cli --cov-report=term-missing

lint:
	$(RUN) ruff check .

lint-fix:
	$(RUN) ruff check --fix .

format:
	$(RUN) ruff format .

format-check:
	$(RUN) ruff format --check .

fix: lint-fix format

typecheck:
	$(RUN) mypy src

check: format-check lint typecheck test

clean:
	rm -rf .coverage .mypy_cache .pytest_cache .ruff_cache .uv-cache build dist htmlcov
