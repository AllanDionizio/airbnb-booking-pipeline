# airbnb-booking-pipeline

> 🚧 Work in progress. This README grows with the code and only describes what is already in the repo.

**Goal:** read Airbnb host emails (booking confirmations and cancellations), parse them and load them into Postgres. So far the repo holds the project scaffolding (packaging, lint, tests, CI) and the two email parsers in [`src/airbnb_booking_pipeline/parsers/`](src/airbnb_booking_pipeline/parsers/): booking confirmations and cancellations.

Bookings priced in USD are converted to BRL in [`src/airbnb_booking_pipeline/enrich/fx.py`](src/airbnb_booking_pipeline/enrich/fx.py) with the Banco Central do Brasil's PTAX sell rate (public Olinda API). The latest quote on or before the requested day covers weekends and holidays. If PTAX is down, an optional `USD_BRL_FALLBACK` rate is used; without it the conversion fails loudly instead of guessing.

## Database

Postgres 16, defined in [`sql/`](sql/) and started with Docker Compose. Bookings go in through one SQL function, [`pipeline.upsert_booking(jsonb)`](sql/002_upsert_booking.sql), called from [`load/postgres.py`](src/airbnb_booking_pipeline/load/postgres.py). The function decides what happens to each confirmation email:

| Result | When |
|---|---|
| `CREATED` / `UPDATED` | New booking, or a newer email for a booking still in `draft` |
| `ALREADY_PROCESSED` | The same email again: the earlier outcome is returned and nothing changes |
| `ALREADY_EXISTS` | The booking left `draft` (a person confirmed it), so the pipeline does not overwrite it |
| `QUEUED_UNKNOWN_LISTING` / `QUEUED_NO_CONTRACT` / `QUEUED_OVERLAP` | Sent to `booking_review_queue` with the original payload, for a person to resolve |
| `INVALID_PAYLOAD` | Malformed input; nothing is written, so the email can be retried |

Cancellation emails go through [`pipeline.register_cancellation(jsonb)`](sql/003_register_cancellation.sql), which records each one in `cancellation_events` and matches it to its booking, or to an open review-queue item when the confirmation is still waiting there. The pipeline never cancels anything: it flags the booking, and a person confirms the cancellation from the `pending_cancellations` view. A penalty is stored as the email states it and in BRL (USD at the PTAX rate). Full refunds carry no amount and no currency.

Idempotency is per email (`processed_messages` for confirmations, `cancellation_events` for cancellations), so replaying an old email never rolls back a newer update. Two exclusion constraints back the rules up: no two live bookings of a listing share a night, and no listing has two contracts in force on the same day. `host_payout` is a generated column, derived from the payout lines.

## Test data

Every email in this repo is synthetic. A small generator in [`src/airbnb_booking_pipeline/synthetic/`](src/airbnb_booking_pipeline/synthetic/) renders Airbnb's Portuguese host email layout from a spec, using fictional listings, `Guest <NATO letter>` names, `HMTEST…` booking codes and made-up amounts. It produces the 18 regression fixtures in [`tests/fixtures/`](tests/fixtures/) (13 confirmations, 5 cancellations), one per layout variant the parsers have to handle: USD amounts, nbsp between symbol and value, stays crossing New Year, discounts and promotions, and the "full refund" button that appears in penalty emails. Each fixture stores the expected parser output, written by hand.

## Development

```bash
python -m venv .venv && source .venv/bin/activate
make setup   # pip install -e ".[dev]" + pre-commit hooks
make lint    # ruff check + ruff format --check
make test    # pytest
make fixtures  # regenerate tests/fixtures/ from the generator

make db-up             # Postgres on localhost:5433, schema loaded from sql/
make test-integration  # integration tests against it
make db-reset          # start over from an empty database
```

## License

[MIT](LICENSE)
