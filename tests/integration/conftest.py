"""
Integration tests against a real Postgres (`make db-up`, then `make test-integration`).

The suite rebuilds the "pipeline" schema from sql/ once per session and
empties every table before each test. Without DATABASE_URL the tests are
skipped, unless REQUIRE_DATABASE is set (as in CI), where that is an error.
"""

import os
from dataclasses import replace
from datetime import date
from pathlib import Path

import psycopg
import pytest

from airbnb_booking_pipeline.parsers import BookingConfirmation, parse_confirmation
from airbnb_booking_pipeline.synthetic import render_confirmation
from airbnb_booking_pipeline.synthetic.scenarios import CONFIRMATIONS

SQL_DIR = Path(__file__).parents[2] / "sql"
TABLES = ["processed_messages", "booking_review_queue", "bookings", "owner_contracts", "listings"]


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        if os.environ.get("REQUIRE_DATABASE"):
            pytest.fail("DATABASE_URL is not set but REQUIRE_DATABASE is")
        pytest.skip("DATABASE_URL is not set: start Postgres with `make db-up`")
    return url


@pytest.fixture(scope="session")
def schema(database_url):
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("drop schema if exists pipeline cascade")
        for path in sorted(SQL_DIR.glob("*.sql")):
            conn.execute(path.read_text(encoding="utf-8"))


@pytest.fixture
def conn(schema, database_url):
    with psycopg.connect(database_url, autocommit=True) as setup:
        tables = ", ".join(f"pipeline.{table}" for table in TABLES)
        setup.execute(f"truncate {tables} restart identity cascade")
    with psycopg.connect(database_url) as conn:
        yield conn


@pytest.fixture
def add_listing(conn):
    """Register a listing with one contract; returns the listing_id."""

    def add(
        title: str,
        valid_from: date = date(2026, 1, 1),
        valid_to: date | None = None,
        commission_pct: float = 20.0,
    ) -> int:
        with conn.transaction():
            (listing_id,) = conn.execute(
                "insert into pipeline.listings (title) values (%s) returning listing_id", [title]
            ).fetchone()
            conn.execute(
                "insert into pipeline.owner_contracts (listing_id, commission_pct, valid_from, valid_to)"
                " values (%s, %s, %s, %s)",
                [listing_id, commission_pct, valid_from, valid_to],
            )
        return listing_id

    return add


@pytest.fixture
def synthetic_booking():
    """Parse a synthetic confirmation by scenario name, then apply `changes`."""

    def build(name: str, **changes) -> BookingConfirmation:
        scenario = next(sc for sc in CONFIRMATIONS if sc.name == name)
        email = render_confirmation(scenario.spec)
        booking = parse_confirmation(email.body, subject=email.subject, email_date=email.email_date)
        return replace(booking, **changes)

    return build
