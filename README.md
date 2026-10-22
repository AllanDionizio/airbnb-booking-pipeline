# airbnb-booking-pipeline

> 🚧 Work in progress. This README grows with the code and only describes what is already in the repo.

**Goal:** read Airbnb host emails (booking confirmations and cancellations), parse them and load them into Postgres. So far the repo holds the project scaffolding (packaging, lint, tests, CI) and the two email parsers in [`src/airbnb_booking_pipeline/parsers/`](src/airbnb_booking_pipeline/parsers/): booking confirmations and cancellations.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
make setup   # pip install -e ".[dev]" + pre-commit hooks
make lint    # ruff check + ruff format --check
make test    # pytest
```

## License

[MIT](LICENSE)
