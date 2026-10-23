"""register_cancellation against a real Postgres: matching, flagging and idempotency."""

from datetime import date
from decimal import Decimal

import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from airbnb_booking_pipeline.enrich import FxRate
from airbnb_booking_pipeline.load import (
    cancellation_payload,
    register_cancellation,
    upsert_booking,
)
from airbnb_booking_pipeline.synthetic.scenarios import CANCELLATIONS

AURORA = "Loft Aurora 101"
CODE = "HMTEST1001"  # the "simple" booking
FX = FxRate(rate=5.1234, quoted_on=date(2026, 4, 10), source="ptax")


def fetch(conn, sql, *params):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def events(conn):
    return fetch(conn, "select * from pipeline.cancellation_events order by event_id")


@pytest.fixture
def booking_id(conn, add_listing, synthetic_booking):
    """A draft booking for CODE."""
    add_listing(AURORA)
    return upsert_booking(conn, synthetic_booking("simple"), message_id="confirmation").booking_id


def set_status(conn, booking_id, status):
    with conn.transaction():
        conn.execute(
            "update pipeline.bookings set status = %s where booking_id = %s", [status, booking_id]
        )


# --- Matching and flagging ------------------------------------------------------


def test_penalty_flags_the_booking_without_cancelling_it(conn, booking_id, synthetic_cancellation):
    notice = synthetic_cancellation("penalty_compact", confirmation_code=CODE)

    result = register_cancellation(conn, notice, message_id="cancel-1")

    assert (result.code, result.booking_id) == ("FLAGGED", booking_id)
    assert result.needs_attention
    [event] = events(conn)
    assert (event["outcome"], event["penalty_amount"], event["currency"]) == (
        "penalty",
        Decimal("315.40"),
        "BRL",
    )
    assert event["penalty_brl"] == Decimal("315.40")
    status = fetch(conn, "select status from pipeline.bookings")[0]["status"]
    assert status == "draft"  # a person confirms the cancellation


def test_full_refund_on_a_confirmed_booking_is_flagged_too(
    conn, booking_id, synthetic_cancellation
):
    set_status(conn, booking_id, "confirmed")
    notice = synthetic_cancellation("full_refund", confirmation_code=CODE)

    result = register_cancellation(conn, notice, message_id="cancel-1")

    assert result.code == "FLAGGED"
    [event] = events(conn)
    assert (event["penalty_amount"], event["currency"], event["penalty_brl"]) == (None, None, None)


def test_usd_penalty_is_stored_as_stated_and_in_brl(conn, booking_id, synthetic_cancellation):
    notice = synthetic_cancellation("penalty_usd", confirmation_code=CODE)

    register_cancellation(conn, notice, message_id="cancel-1", fx=FX)

    [event] = events(conn)
    assert (event["penalty_amount"], event["currency"]) == (Decimal("1418.70"), "USD")
    assert event["penalty_brl"] == Decimal("7268.57")  # 1418.70 x 5.1234 = 7268.5664
    assert (event["fx_rate"], event["fx_source"]) == (Decimal("5.1234"), "ptax")


def test_an_already_cancelled_booking_only_gets_the_event(conn, booking_id, synthetic_cancellation):
    set_status(conn, booking_id, "cancelled")
    notice = synthetic_cancellation("full_refund", confirmation_code=CODE)

    result = register_cancellation(conn, notice, message_id="cancel-1")

    assert result.code == "ALREADY_CANCELLED"
    assert not result.needs_attention
    assert events(conn)[0]["target"] == "already_cancelled"


def test_a_cancellation_for_a_queued_email_flags_the_review_item(
    conn, synthetic_booking, synthetic_cancellation
):
    # No listing registered: the confirmation went to the review queue
    queued = upsert_booking(conn, synthetic_booking("simple"), message_id="confirmation")
    notice = synthetic_cancellation("penalty_nbsp", confirmation_code=CODE)

    result = register_cancellation(conn, notice, message_id="cancel-1")

    assert (result.code, result.review_id) == ("FLAGGED_REVIEW_ITEM", queued.review_id)
    assert events(conn)[0]["target"] == "review_item"


@pytest.mark.parametrize("status", ["resolved", "discarded"])
def test_a_closed_review_item_is_not_flagged(
    conn, synthetic_booking, synthetic_cancellation, status
):
    upsert_booking(conn, synthetic_booking("simple"), message_id="confirmation")
    with conn.transaction():
        conn.execute("update pipeline.booking_review_queue set status = %s", [status])
    notice = synthetic_cancellation("full_refund", confirmation_code=CODE)

    result = register_cancellation(conn, notice, message_id="cancel-1")

    assert result.code == "NOT_FOUND"


def test_a_cancellation_that_matches_nothing_is_kept(conn, synthetic_cancellation):
    result = register_cancellation(
        conn, synthetic_cancellation("full_refund"), message_id="cancel-1"
    )

    assert result.code == "NOT_FOUND"
    assert result.needs_attention
    assert events(conn)[0]["target"] == "not_found"


def test_pending_cancellations_lists_flagged_live_bookings(
    conn, booking_id, synthetic_cancellation
):
    notice = synthetic_cancellation("penalty_compact", confirmation_code=CODE)
    register_cancellation(conn, notice, message_id="cancel-1")

    [pending] = fetch(conn, "select * from pipeline.pending_cancellations")
    assert (pending["booking_id"], pending["outcome"], pending["penalty_brl"]) == (
        booking_id,
        "penalty",
        Decimal("315.40"),
    )

    # Once a person cancels the booking, it leaves the list
    set_status(conn, booking_id, "cancelled")
    assert fetch(conn, "select * from pipeline.pending_cancellations") == []


def test_the_newest_email_wins_in_pending_cancellations(conn, booking_id, synthetic_cancellation):
    register_cancellation(
        conn, synthetic_cancellation("full_refund", confirmation_code=CODE), message_id="cancel-1"
    )
    register_cancellation(
        conn,
        synthetic_cancellation("penalty_compact", confirmation_code=CODE),
        message_id="cancel-2",
    )

    [pending] = fetch(conn, "select * from pipeline.pending_cancellations")
    assert pending["outcome"] == "penalty"


# --- Idempotency ----------------------------------------------------------------


def test_the_same_email_twice_records_one_event(conn, booking_id, synthetic_cancellation):
    notice = synthetic_cancellation("penalty_compact", confirmation_code=CODE)

    first = register_cancellation(conn, notice, message_id="cancel-1")
    second = register_cancellation(conn, notice, message_id="cancel-1")

    assert second.code == "ALREADY_PROCESSED"
    assert (second.event_id, second.booking_id) == (first.event_id, booking_id)
    assert len(events(conn)) == 1


def test_every_synthetic_cancellation_is_recorded_once(conn, synthetic_cancellation):
    def load_all():
        codes = []
        for scenario in CANCELLATIONS:
            notice = synthetic_cancellation(scenario.name)
            fx = FX if notice.currency == "USD" else None
            codes.append(register_cancellation(conn, notice, message_id=scenario.name, fx=fx).code)
        return codes

    # No bookings were loaded, so nothing matches
    assert load_all() == ["NOT_FOUND"] * len(CANCELLATIONS)
    assert load_all() == ["ALREADY_PROCESSED"] * len(CANCELLATIONS)
    assert len(events(conn)) == len(CANCELLATIONS)


# --- Rejected payloads ----------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"outcome": "refunded"}, id="unknown-outcome"),
        pytest.param({"penalty_amount": None}, id="penalty-without-amount"),
        pytest.param({"penalty_amount": -5}, id="negative-penalty"),
        pytest.param({"penalty_amount": "a lot"}, id="amount-not-a-number"),
        pytest.param({"currency": None}, id="penalty-without-currency"),
        pytest.param({"currency": "EUR"}, id="unknown-currency"),
        pytest.param({"currency": "USD"}, id="usd-without-rate"),
        pytest.param({"outcome": "full_refund"}, id="refund-with-amount"),
        pytest.param({"message_id": None}, id="no-message-id"),
    ],
)
def test_malformed_payloads_are_rejected_and_not_recorded(conn, synthetic_cancellation, change):
    payload = (
        cancellation_payload(synthetic_cancellation("penalty_compact"), message_id="cancel-1")
        | change
    )

    with conn.transaction():
        result = conn.execute(
            "select pipeline.register_cancellation(%s)", [Jsonb(payload)]
        ).fetchone()[0]

    assert result["code"] == "INVALID_PAYLOAD"
    assert events(conn) == []
