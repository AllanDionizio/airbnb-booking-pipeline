.PHONY: setup lint format test test-integration fixtures inbox db-up db-down db-reset

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

# Needs the database from `make db-up`
DATABASE_URL ?= postgresql://pipeline:pipeline@localhost:5433/bookings
test-integration:
	DATABASE_URL=$(DATABASE_URL) pytest -q tests/integration

fixtures:
	python -m airbnb_booking_pipeline.synthetic tests/fixtures

inbox:
	python -m airbnb_booking_pipeline.synthetic.inbox data/sample_inbox

db-up:
	docker compose up -d --wait

db-down:
	docker compose down

db-reset:
	docker compose down -v && docker compose up -d --wait
