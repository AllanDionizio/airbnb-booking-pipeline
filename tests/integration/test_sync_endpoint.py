"""
The web app as the container starts it, from the environment alone, over
the demo inbox and a real database.
"""

from pathlib import Path

import pytest

from airbnb_booking_pipeline import runner
from airbnb_booking_pipeline.enrich import FxRate
from airbnb_booking_pipeline.web import create_app

DATA = Path(__file__).parents[2] / "data"
TABLES = ["bookings", "booking_review_queue", "processed_messages", "cancellation_events"]


class FixedRates:
    def usd_brl(self, day):
        return FxRate(rate=5.1234, quoted_on=day, source="ptax")

    @classmethod
    def from_env(cls):
        return cls()


@pytest.fixture
def app_client(conn, database_url, monkeypatch):
    with conn.transaction():
        conn.execute((DATA / "demo_seed.sql").read_text(encoding="utf-8"))
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("INBOX_DIR", str(DATA / "sample_inbox"))
    monkeypatch.setattr(runner, "PtaxClient", FixedRates)
    return create_app().test_client()


def row_counts(conn):
    return {t: conn.execute(f"select count(*) from pipeline.{t}").fetchone()[0] for t in TABLES}


def test_sync_loads_the_inbox_once(conn, app_client):
    first = app_client.post("/sync")
    before = row_counts(conn)
    second = app_client.post("/sync")

    assert first.status_code == second.status_code == 200
    assert first.json["processed"] == second.json["processed"] == 22
    assert before["bookings"] > 0
    # Written by hand, as in test_demo.py: the second run loads nothing
    assert second.json["counts"] == [
        {"kind": "booking", "code": "ALREADY_PROCESSED", "emails": 16},
        {"kind": "booking", "code": "PARSE_ERROR", "emails": 1},
        {"kind": "cancellation", "code": "ALREADY_PROCESSED", "emails": 4},
        {"kind": "unrecognized", "code": "UNRECOGNIZED", "emails": 1},
    ]
    assert row_counts(conn) == before


def test_a_database_that_does_not_answer_is_a_503(app_client, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://pipeline:pipeline@127.0.0.1:1/bookings")

    response = create_app().test_client().post("/sync")

    assert (response.status_code, response.json) == (503, {"error": "database error"})
