.PHONY: setup lint format test

# Use the project's virtualenv when there is one, activated or not (CI
# installs into its own Python and has none)
ifneq ($(wildcard $(CURDIR)/.venv/bin/python),)
export VIRTUAL_ENV := $(CURDIR)/.venv
export PATH := $(CURDIR)/.venv/bin:$(PATH)
endif

setup:
	pip install -e ".[dev]"
	pre-commit install

lint:
	ruff check . && ruff format --check .

format:
	ruff check --fix . && ruff format .

test:
	pytest -q
