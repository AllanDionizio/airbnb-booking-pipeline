# airbnb-booking-pipeline

> 🚧 Work in progress. This README grows with the code and only describes what is already in the repo.

**Goal:** read Airbnb host emails (booking confirmations and cancellations), parse them and load them into Postgres. So far the repo holds the project scaffolding (packaging, lint, tests, CI) and the two email parsers in [`src/airbnb_booking_pipeline/parsers/`](src/airbnb_booking_pipeline/parsers/): booking confirmations and cancellations.

## Test data

Every email in this repo is synthetic. A small generator in [`src/airbnb_booking_pipeline/synthetic/`](src/airbnb_booking_pipeline/synthetic/) renders Airbnb's Portuguese host email layout from a spec, using fictional listings, `Guest <NATO letter>` names, `HMTEST…` booking codes and made-up amounts. It produces the 18 regression fixtures in [`tests/fixtures/`](tests/fixtures/) (13 confirmations, 5 cancellations), one per layout variant the parsers have to handle: USD amounts, nbsp between symbol and value, stays crossing New Year, discounts and promotions, and the "full refund" button that appears in penalty emails. Each fixture stores the expected parser output, written by hand.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
make setup   # pip install -e ".[dev]" + pre-commit hooks
make lint    # ruff check + ruff format --check
make test    # pytest
make fixtures  # regenerate tests/fixtures/ from the generator
```

## License

[MIT](LICENSE)
