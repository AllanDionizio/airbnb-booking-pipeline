"""The whole run against Postgres: .eml files in a directory → pipeline → database, and the CLI."""

import io
from dataclasses import replace
from datetime import date, timedelta

import pytest

from airbnb_booking_pipeline import cli
from airbnb_booking_pipeline.enrich import FxRate
from airbnb_booking_pipeline.load import PostgresStore
from airbnb_booking_pipeline.pipeline import Kind, Pipeline
from airbnb_booking_pipeline.sources import LocalSource
from airbnb_booking_pipeline.synthetic import (
    LISTINGS,
    render_cancellation,
    render_confirmation,
    to_eml,
)
from airbnb_booking_pipeline.synthetic.scenarios import CANCELLATIONS, CONFIRMATIONS

FX = FxRate(rate=5.1234, quoted_on=date(2026, 6, 30), source="ptax")


class FixedRates:
    """No network in the tests: every day gets the same rate."""

    def usd_brl(self, day):
        return FX

    @classmethod
    def from_env(cls):
        return cls()


@pytest.fixture
def inbox(tmp_path):
    """The 18 scenarios as .eml files, plus a cancellation of the "simple" booking."""
    rendered = [render_confirmation(sc.spec) for sc in CONFIRMATIONS]
    rendered += [render_cancellation(sc.spec) for sc in CANCELLATIONS]

    simple = next(sc.spec for sc in CONFIRMATIONS if sc.name == "simple")
    refund = next(sc.spec for sc in CANCELLATIONS if sc.name == "full_refund")
    rendered.append(
        render_cancellation(
            replace(
                refund,
                code=simple.code,
                listing=simple.listing,
                check_in=simple.check_in,
                check_out=simple.check_out,
                email_date=simple.email_date + timedelta(days=3),
            )
        )
    )

    for n, email in enumerate(rendered, start=1):
        (tmp_path / f"{n:02d}.eml").write_bytes(to_eml(email, f"<{n:02d}@example.invalid>"))
    return tmp_path


@pytest.fixture
def listings(add_listing):
    for listing in LISTINGS:
        add_listing(listing.name)


def test_loads_a_directory_of_emails(conn, listings, inbox):
    pipeline = Pipeline(PostgresStore(conn), FixedRates())

    report = pipeline.run(LocalSource(inbox).fetch())

    assert report.counts() == {
        (Kind.BOOKING, "CREATED"): 13,
        (Kind.CANCELLATION, "FLAGGED"): 1,  # the "simple" booking
        (Kind.CANCELLATION, "NOT_FOUND"): 5,  # their bookings are not in the inbox
    }
    assert conn.execute("select count(*) from pipeline.bookings").fetchone()[0] == 13
    pending = conn.execute(
        "select confirmation_code, outcome from pipeline.pending_cancellations"
    ).fetchall()
    assert pending == [("HMTEST1001", "full_refund")]
    [(rate,)] = conn.execute(
        "select fx_rate from pipeline.bookings where original_currency = 'USD'"
    ).fetchall()
    assert float(rate) == FX.rate


def test_the_cli_runs_the_inbox_and_prints_a_summary(
    conn, listings, inbox, database_url, monkeypatch
):
    monkeypatch.setattr(cli, "PtaxClient", FixedRates)
    out = io.StringIO()

    exit_code = cli.main(["run", "--inbox", str(inbox), "--database-url", database_url], out=out)

    assert exit_code == 0
    text = out.getvalue()
    assert text.startswith("19 emails processed\n")
    assert "Needs attention (6):" in text
    assert "HMTEST1001" in text
