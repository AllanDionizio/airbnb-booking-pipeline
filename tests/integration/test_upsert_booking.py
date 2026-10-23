"""upsert_booking against a real Postgres: idempotency, quarantine and the schema's guarantees."""

from datetime import date
from decimal import Decimal

import psycopg
import pytest
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from airbnb_booking_pipeline.enrich import FxRate, convert_booking
from airbnb_booking_pipeline.load import booking_payload, upsert_booking
from airbnb_booking_pipeline.synthetic import LISTINGS
from airbnb_booking_pipeline.synthetic.scenarios import CONFIRMATIONS

AURORA = "Loft Aurora 101"  # the listing of the "simple" scenario: 12/06 to 14/06/2026
FX = FxRate(rate=5.1234, quoted_on=date(2026, 7, 1), source="ptax")


def fetch(conn, sql, *params):
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def count(conn, table):
    return conn.execute(f"select count(*) from pipeline.{table}").fetchone()[0]


def get_booking(conn, booking_id):
    return fetch(conn, "select * from pipeline.bookings where booking_id = %s", booking_id)[0]


def set_status(conn, booking_id, status):
    with conn.transaction():
        conn.execute(
            "update pipeline.bookings set status = %s where booking_id = %s", [status, booking_id]
        )


# --- Happy path and idempotency -------------------------------------------------


def test_creates_a_draft_booking(conn, add_listing, synthetic_booking):
    add_listing(AURORA)

    result = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    assert result.code == "CREATED"
    assert not result.needs_review
    booking = get_booking(conn, result.booking_id)
    assert booking["status"] == "draft"
    assert booking["confirmation_code"] == "HMTEST1001"
    assert (booking["check_in"], booking["check_out"]) == (date(2026, 6, 12), date(2026, 6, 14))
    # Derived in the database: 480.00 + 90.00 - 23.94
    assert booking["host_payout"] == Decimal("546.06")
    assert booking["original_currency"] == "BRL"
    assert booking["fx_rate"] is None


def test_the_same_email_twice_changes_nothing(conn, add_listing, synthetic_booking):
    add_listing(AURORA)

    first = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")
    second = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    assert second.code == "ALREADY_PROCESSED"
    assert second.booking_id == first.booking_id
    assert count(conn, "bookings") == count(conn, "processed_messages") == 1


def test_a_new_email_for_a_draft_updates_it(conn, add_listing, synthetic_booking):
    add_listing(AURORA)
    first = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    changed = synthetic_booking("simple", check_out=date(2026, 6, 15))
    result = upsert_booking(conn, changed, message_id="msg-2")

    assert (result.code, result.booking_id) == ("UPDATED", first.booking_id)
    booking = get_booking(conn, first.booking_id)
    assert booking["check_out"] == date(2026, 6, 15)
    assert booking["last_message_id"] == "msg-2"


def test_replaying_an_older_email_does_not_roll_back_an_update(
    conn, add_listing, synthetic_booking
):
    add_listing(AURORA)
    first = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")
    upsert_booking(
        conn, synthetic_booking("simple", check_out=date(2026, 6, 15)), message_id="msg-2"
    )

    replay = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    assert replay.code == "ALREADY_PROCESSED"
    assert get_booking(conn, first.booking_id)["check_out"] == date(2026, 6, 15)


def test_a_booking_out_of_draft_is_never_overwritten(conn, add_listing, synthetic_booking):
    add_listing(AURORA)
    first = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")
    set_status(conn, first.booking_id, "confirmed")

    changed = synthetic_booking("simple", check_out=date(2026, 6, 15))
    result = upsert_booking(conn, changed, message_id="msg-2")

    assert result.code == "ALREADY_EXISTS"
    assert get_booking(conn, first.booking_id)["check_out"] == date(2026, 6, 14)


def test_usd_booking_is_stored_in_brl_with_its_rate(conn, add_listing, synthetic_booking):
    add_listing("Studio Eclipse 505")
    booking = convert_booking(synthetic_booking("usd"), FX)

    result = upsert_booking(conn, booking, message_id="msg-1", fx=FX)

    row = get_booking(conn, result.booking_id)
    assert row["original_currency"] == "USD"
    assert (row["fx_rate"], row["fx_quoted_on"], row["fx_source"]) == (
        Decimal("5.1234"),
        date(2026, 7, 1),
        "ptax",
    )
    assert row["accommodation"] == Decimal("1460.17")
    assert row["host_payout"] == Decimal("1481.69")


def test_every_synthetic_confirmation_loads_once(conn, add_listing, synthetic_booking):
    for listing in LISTINGS:
        add_listing(listing.name)

    def load_all():
        codes = []
        for scenario in CONFIRMATIONS:
            booking = synthetic_booking(scenario.name)
            fx = FX if booking.currency == "USD" else None
            if fx:
                booking = convert_booking(booking, fx)
            codes.append(upsert_booking(conn, booking, message_id=scenario.name, fx=fx).code)
        return codes

    assert load_all() == ["CREATED"] * len(CONFIRMATIONS)
    assert load_all() == ["ALREADY_PROCESSED"] * len(CONFIRMATIONS)
    assert count(conn, "bookings") == len(CONFIRMATIONS)


# --- Quarantine -----------------------------------------------------------------


def test_unknown_listing_goes_to_review(conn, synthetic_booking):
    result = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    assert result.code == "QUEUED_UNKNOWN_LISTING"
    assert result.needs_review
    [item] = fetch(conn, "select * from pipeline.booking_review_queue")
    assert (item["review_id"], item["reason"], item["status"]) == (
        result.review_id,
        "unknown_listing",
        "open",
    )
    assert item["payload"]["message_id"] == "msg-1"  # kept whole, to replay later
    assert count(conn, "bookings") == 0


def test_listing_title_match_ignores_case_and_outer_spaces(conn, add_listing, synthetic_booking):
    add_listing("  loft AURORA 101 ")

    result = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    assert result.code == "CREATED"


def test_no_contract_in_force_at_check_in_goes_to_review(conn, add_listing, synthetic_booking):
    # valid_to is exclusive: the contract ends the day the guest checks in
    add_listing(AURORA, valid_from=date(2026, 1, 1), valid_to=date(2026, 6, 12))

    result = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    assert result.code == "QUEUED_NO_CONTRACT"


def test_overlapping_dates_go_to_review(conn, add_listing, synthetic_booking):
    add_listing(AURORA)
    first = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    clash = synthetic_booking(
        "simple",
        confirmation_code="HMTEST9001",
        check_in=date(2026, 6, 13),
        check_out=date(2026, 6, 16),
    )
    result = upsert_booking(conn, clash, message_id="msg-2")

    assert result.code == "QUEUED_OVERLAP"
    [item] = fetch(conn, "select * from pipeline.booking_review_queue")
    assert item["conflicting_booking_ids"] == [first.booking_id]


def test_back_to_back_stays_do_not_overlap(conn, add_listing, synthetic_booking):
    add_listing(AURORA)
    upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    # Checks in on the day the first guest checks out
    next_stay = synthetic_booking(
        "simple",
        confirmation_code="HMTEST9001",
        check_in=date(2026, 6, 14),
        check_out=date(2026, 6, 16),
    )

    assert upsert_booking(conn, next_stay, message_id="msg-2").code == "CREATED"


def test_a_cancelled_booking_frees_its_dates(conn, add_listing, synthetic_booking):
    add_listing(AURORA)
    first = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")
    set_status(conn, first.booking_id, "cancelled")

    same_dates = synthetic_booking("simple", confirmation_code="HMTEST9001")

    assert upsert_booking(conn, same_dates, message_id="msg-2").code == "CREATED"


def test_a_queued_email_is_queued_only_once(conn, synthetic_booking):
    first = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")
    second = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")

    assert second.code == "ALREADY_PROCESSED"
    assert second.review_id == first.review_id
    assert count(conn, "booking_review_queue") == 1


# --- Rejected payloads ----------------------------------------------------------


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"check_out": "2026-06-12"}, id="checkout-not-after-checkin"),
        pytest.param({"check_in": "2026-06-31"}, id="impossible-date"),
        pytest.param({"message_id": " "}, id="blank-message-id"),
        pytest.param({"payout_lines": {"accommodation": 0}}, id="no-accommodation"),
        pytest.param({"payout_lines": {"accommodation": 480, "tip": 10}}, id="unknown-line"),
        pytest.param({"original_currency": "USD"}, id="usd-without-rate"),
    ],
)
def test_malformed_payloads_are_rejected_and_not_recorded(
    conn, add_listing, synthetic_booking, change
):
    add_listing(AURORA)
    payload = booking_payload(synthetic_booking("simple"), message_id="msg-1") | change

    with conn.transaction():
        result = conn.execute("select pipeline.upsert_booking(%s)", [Jsonb(payload)]).fetchone()[0]

    assert result["code"] == "INVALID_PAYLOAD"
    assert count(conn, "processed_messages") == 0  # free to retry once the loader is fixed


# --- Guarantees of the schema itself --------------------------------------------


def test_a_listing_cannot_have_two_contracts_in_force_on_the_same_day(conn, add_listing):
    listing_id = add_listing(AURORA, valid_from=date(2026, 1, 1))  # open-ended

    with pytest.raises(psycopg.errors.ExclusionViolation), conn.transaction():
        conn.execute(
            "insert into pipeline.owner_contracts (listing_id, commission_pct, valid_from)"
            " values (%s, 15, '2026-06-01')",
            [listing_id],
        )


def test_overlapping_live_bookings_are_rejected_even_outside_the_function(
    conn, add_listing, synthetic_booking
):
    add_listing(AURORA)
    first = upsert_booking(conn, synthetic_booking("simple"), message_id="msg-1")
    booking = get_booking(conn, first.booking_id)

    # A second writer that skips upsert_booking hits the exclusion constraint
    with pytest.raises(psycopg.errors.ExclusionViolation), conn.transaction():
        conn.execute(
            "insert into pipeline.bookings (platform, confirmation_code, listing_id, contract_id,"
            " guest_name, check_in, check_out, accommodation, original_currency, last_message_id)"
            " values ('airbnb', 'HMTEST9001', %s, %s, 'Guest Zulu', '2026-06-13', '2026-06-15',"
            " 100, 'BRL', 'manual')",
            [booking["listing_id"], booking["contract_id"]],
        )
