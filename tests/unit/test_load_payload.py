"""The upsert_booking and register_cancellation payloads, built without a database."""

from datetime import date

import pytest

from airbnb_booking_pipeline.enrich import FxRate, convert_booking
from airbnb_booking_pipeline.load import LoadResult, booking_payload, cancellation_payload
from airbnb_booking_pipeline.parsers import parse_cancellation, parse_confirmation
from airbnb_booking_pipeline.synthetic import render_cancellation, render_confirmation
from airbnb_booking_pipeline.synthetic.scenarios import CANCELLATIONS, CONFIRMATIONS

FX = FxRate(rate=5.1234, quoted_on=date(2026, 7, 1), source="ptax")


def parsed(name):
    scenario = next(sc for sc in CONFIRMATIONS if sc.name == name)
    email = render_confirmation(scenario.spec)
    return parse_confirmation(email.body, subject=email.subject, email_date=email.email_date)


def test_brl_booking_payload():
    payload = booking_payload(parsed("simple"), message_id="msg-1")

    assert payload == {
        "message_id": "msg-1",
        "platform": "airbnb",
        "confirmation_code": "HMTEST1001",
        "listing_title": "Loft Aurora 101",
        "guest_name": "Guest Alpha",
        "num_guests": 2,
        "check_in": "2026-06-12",
        "check_out": "2026-06-14",
        "payout_lines": {"accommodation": 480.0, "cleaning_fee": 90.0, "host_service_fee": 23.94},
        "original_currency": "BRL",
        "fx": None,
    }


def test_converted_usd_booking_carries_its_rate():
    payload = booking_payload(convert_booking(parsed("usd"), FX), message_id="msg-1", fx=FX)

    assert payload["original_currency"] == "USD"
    assert payload["fx"] == {"rate": 5.1234, "quoted_on": "2026-07-01", "source": "ptax"}


def test_usd_booking_must_be_converted_first():
    with pytest.raises(ValueError, match="convert"):
        booking_payload(parsed("usd"), message_id="msg-1")


@pytest.mark.parametrize(
    ("code", "needs_review"),
    [("CREATED", False), ("ALREADY_EXISTS", False), ("QUEUED_OVERLAP", True)],
)
def test_needs_review_only_for_queued_results(code, needs_review):
    assert LoadResult(code=code, message="").needs_review is needs_review


def parsed_cancellation(name):
    scenario = next(sc for sc in CANCELLATIONS if sc.name == name)
    email = render_cancellation(scenario.spec)
    return parse_cancellation(email.body, subject=email.subject)


def test_cancellation_payload():
    payload = cancellation_payload(parsed_cancellation("penalty_compact"), message_id="msg-1")

    assert payload == {
        "message_id": "msg-1",
        "platform": "airbnb",
        "confirmation_code": "HMTEST2001",
        "outcome": "penalty",
        "penalty_amount": 315.4,
        "currency": "BRL",
        "fx": None,
    }


def test_full_refund_payload_has_no_amount_and_ignores_a_rate():
    payload = cancellation_payload(parsed_cancellation("full_refund"), message_id="msg-1", fx=FX)

    assert (payload["penalty_amount"], payload["currency"], payload["fx"]) == (None, None, None)


def test_usd_penalty_needs_a_rate():
    with pytest.raises(ValueError, match="rate"):
        cancellation_payload(parsed_cancellation("penalty_usd"), message_id="msg-1")
