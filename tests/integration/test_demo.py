"""
The demo from scratch: data/demo_seed.sql, then data/sample_inbox/ twice.

The expected codes are written by hand from the story in synthetic/inbox.py,
not computed from it.
"""

from pathlib import Path

import pytest

from airbnb_booking_pipeline.enrich import FxRate
from airbnb_booking_pipeline.load import PostgresStore
from airbnb_booking_pipeline.pipeline import Kind, Pipeline
from airbnb_booking_pipeline.sources import LocalSource

DATA = Path(__file__).parents[2] / "data"
TABLES = ["bookings", "booking_review_queue", "processed_messages", "cancellation_events"]


class FixedRates:
    def usd_brl(self, day):
        return FxRate(rate=5.1234, quoted_on=day, source="ptax")


@pytest.fixture
def demo(conn):
    with conn.transaction():
        conn.execute((DATA / "demo_seed.sql").read_text(encoding="utf-8"))
    pipeline = Pipeline(PostgresStore(conn), FixedRates())
    return lambda: pipeline.run(LocalSource(DATA / "sample_inbox").fetch())


def row_counts(conn):
    return {t: conn.execute(f"select count(*) from pipeline.{t}").fetchone()[0] for t in TABLES}


def test_the_first_run_walks_every_path(conn, demo):
    report = demo()

    assert report.counts() == {
        (Kind.BOOKING, "CREATED"): 12,
        (Kind.BOOKING, "QUEUED_OVERLAP"): 1,  # HMTEST3009, double booking at Casa Cedro
        (Kind.BOOKING, "QUEUED_NO_CONTRACT"): 1,  # HMTEST3012, after the Duna contract
        (Kind.BOOKING, "QUEUED_UNKNOWN_LISTING"): 2,  # Flat Fênix 606 is not registered
        (Kind.BOOKING, "PARSE_ERROR"): 1,  # HMTEST3017, an unknown payout line
        (Kind.CANCELLATION, "FLAGGED"): 2,  # HMTEST3002, and HMTEST3014 with a USD penalty
        (Kind.CANCELLATION, "FLAGGED_REVIEW_ITEM"): 1,  # HMTEST3015, waiting in the queue
        (Kind.CANCELLATION, "NOT_FOUND"): 1,  # HMTEST3099, older than the inbox
        (Kind.UNRECOGNIZED, "UNRECOGNIZED"): 1,  # a guest message
    }
    pending = conn.execute(
        "select confirmation_code, outcome from pipeline.pending_cancellations"
        " order by confirmation_code"
    ).fetchall()
    assert pending == [("HMTEST3002", "full_refund"), ("HMTEST3014", "penalty")]


def test_the_second_run_loads_nothing(conn, demo):
    demo()
    before = row_counts(conn)

    report = demo()

    assert report.counts() == {
        (Kind.BOOKING, "ALREADY_PROCESSED"): 16,
        (Kind.CANCELLATION, "ALREADY_PROCESSED"): 4,
        # These never reach the database, so they come back until someone acts
        (Kind.BOOKING, "PARSE_ERROR"): 1,
        (Kind.UNRECOGNIZED, "UNRECOGNIZED"): 1,
    }
    assert row_counts(conn) == before


def test_usd_bookings_are_loaded_with_their_rate(conn, demo):
    demo()

    rows = conn.execute(
        "select confirmation_code, fx_source from pipeline.bookings"
        " where original_currency = 'USD' order by confirmation_code"
    ).fetchall()
    assert rows == [("HMTEST3013", "ptax"), ("HMTEST3014", "ptax")]
