# airbnb-booking-pipeline

> 🚧 Work in progress. This README grows with the code and only describes what is already in the repo.

**Goal:** read Airbnb host emails (booking confirmations and cancellations), parse them and load them into Postgres. The two email parsers, for booking confirmations and cancellations, live in [`src/airbnb_booking_pipeline/parsers/`](src/airbnb_booking_pipeline/parsers/).

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

## Pipeline

[`pipeline.py`](src/airbnb_booking_pipeline/pipeline.py) runs each email through the same steps: route it by subject (a cancellation without a subject is recognized by its body), parse it, convert USD amounts with the PTAX rate of the day the email arrived in `BUSINESS_TZ` (default `America/Sao_Paulo`), and load it. Emails are processed oldest first, so a newer email for the same booking wins.

Every email ends with a result code: the database's codes above, or one of three the pipeline sets itself when the email never reaches the database (`UNRECOGNIZED`, `PARSE_ERROR`, `FX_ERROR`). A bad email is reported and the run goes on; a database error stops the run, and rerunning is safe because every email is idempotent.

Emails come from a source ([`sources/`](src/airbnb_booking_pipeline/sources/)). For now that is `LocalSource`, a directory of `.eml` files, with the `Message-ID` header as the idempotency key. The command line runs one:

```bash
airbnb-pipeline run --inbox path/to/emails   # uses $DATABASE_URL
```

It prints how many emails ended in each code, then the ones a person has to look at (review queue, unmatched or undetermined cancellations, errors). `-v` logs every email.

## Test data

Every email in this repo is synthetic. A small generator in [`src/airbnb_booking_pipeline/synthetic/`](src/airbnb_booking_pipeline/synthetic/) renders Airbnb's Portuguese host email layout from a spec, using fictional listings, `Guest <NATO letter>` names, `HMTEST…` booking codes and made-up amounts. It produces the 18 regression fixtures in [`tests/fixtures/`](tests/fixtures/) (13 confirmations, 5 cancellations), one per layout variant the parsers have to handle: USD amounts, nbsp between symbol and value, stays crossing New Year, discounts and promotions, and the "full refund" button that appears in penalty emails. Each fixture stores the expected parser output, written by hand.

## Demo

```bash
python -m venv .venv && source .venv/bin/activate
make setup && make demo   # needs Docker
```

`make demo` starts Postgres from an empty volume, loads the demo listings and contracts ([`data/demo_seed.sql`](data/demo_seed.sql)) and runs the pipeline over [`data/sample_inbox/`](data/sample_inbox/): 22 synthetic emails from a few months of a host's mailbox. The inbox is built to reach every result code: bookings in BRL and USD, a double booking, a stay after its owner contract ends, a listing nobody registered, cancellations with and without a penalty, one for a booking that is still in the review queue, one for a booking older than the inbox, a payout line the parser has never seen and a guest message. The run ends with this summary:

```text
22 emails processed
  booking       CREATED                    12
  booking       PARSE_ERROR                 1
  booking       QUEUED_NO_CONTRACT          1
  booking       QUEUED_OVERLAP              1
  booking       QUEUED_UNKNOWN_LISTING      2
  cancellation  FLAGGED                     2
  cancellation  FLAGGED_REVIEW_ITEM         1
  cancellation  NOT_FOUND                   1
  unrecognized  UNRECOGNIZED                1
```

followed by the 10 emails that need a person. Run `airbnb-pipeline run --inbox data/sample_inbox` again and every email that reached the database comes back as `ALREADY_PROCESSED`, with no new rows. The USD bookings use the live PTAX rate, so they need network access (or `USD_BRL_FALLBACK`).

## Development

```bash
python -m venv .venv && source .venv/bin/activate
make setup   # pip install -e ".[dev]" + pre-commit hooks
make lint    # ruff check + ruff format --check
make test    # pytest
make fixtures  # regenerate tests/fixtures/ from the generator
make inbox     # regenerate data/sample_inbox/ from the generator

make db-up             # Postgres on localhost:5433, schema loaded from sql/
make test-integration  # integration tests against it
make db-reset          # start over from an empty database
```

## License

[MIT](LICENSE)
