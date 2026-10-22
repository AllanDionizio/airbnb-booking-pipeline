# airbnb-booking-pipeline

> 🚧 Work in progress. This README grows with the code and only describes what is already in the repo.

**Goal:** read Airbnb host emails (booking confirmations and cancellations), parse them and load them into Postgres. For now the repo holds only the project scaffolding (packaging, lint, tests, CI).

## Development

```bash
python -m venv .venv && source .venv/bin/activate
make setup   # pip install -e ".[dev]" + pre-commit hooks
make lint    # ruff check + ruff format --check
make test    # pytest
```

## License

[MIT](LICENSE)
