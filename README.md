# airbnb-booking-pipeline

> 🚧 Work in progress. This README grows with the code and only describes what is already in the repo.

A pipeline that reads Airbnb host emails (booking confirmations and cancellations), parses them and loads them into Postgres.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
make setup   # pip install -e ".[dev]" + pre-commit hooks
make lint    # ruff check + ruff format --check
make test    # pytest
```

## License

[MIT](LICENSE)
