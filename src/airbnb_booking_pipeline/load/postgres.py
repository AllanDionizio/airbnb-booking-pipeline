"""
The one module that knows the database contract.

Bookings go in through the SQL function pipeline.upsert_booking(jsonb), which
owns idempotency and quarantine (see sql/002_upsert_booking.sql). This module
only builds its payload and reads its answer, so the rules live in one place
and every client of the database gets them.
"""

from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb

from ..enrich import FxRate
from ..parsers import BookingConfirmation


@dataclass(frozen=True)
class LoadResult:
    code: str  # CREATED, UPDATED, ALREADY_PROCESSED, ALREADY_EXISTS, QUEUED_*, INVALID_PAYLOAD
    message: str
    booking_id: int | None = None
    review_id: int | None = None

    @property
    def needs_review(self) -> bool:
        """True when the email went to the review queue."""
        return self.code.startswith("QUEUED_")


def booking_payload(
    booking: BookingConfirmation,
    *,
    message_id: str,
    fx: FxRate | None = None,
    platform: str = "airbnb",
) -> dict:
    """
    Build the upsert_booking payload.

    `booking` must already be in BRL. Pass the `fx` used by
    enrich.convert_booking for a booking that came in USD, so the database
    records the rate.
    """
    if booking.currency != "BRL":
        raise ValueError(
            f"Bookings are loaded in BRL; convert this {booking.currency} booking first"
        )
    return {
        "message_id": message_id,
        "platform": platform,
        "confirmation_code": booking.confirmation_code,
        "listing_title": booking.listing_name,
        "guest_name": booking.guest_name,
        "num_guests": booking.num_guests,
        "check_in": booking.check_in.isoformat(),
        "check_out": booking.check_out.isoformat(),
        # host_payout is not sent: the database derives it from the lines
        "payout_lines": booking.payout_lines,
        "original_currency": "USD" if fx else "BRL",
        "fx": (
            {"rate": fx.rate, "quoted_on": fx.quoted_on.isoformat(), "source": fx.source}
            if fx
            else None
        ),
    }


def upsert_booking(
    conn: psycopg.Connection,
    booking: BookingConfirmation,
    *,
    message_id: str,
    fx: FxRate | None = None,
) -> LoadResult:
    """Load one booking in its own transaction and return what the database did."""
    payload = booking_payload(booking, message_id=message_id, fx=fx)
    with conn.transaction():
        row = conn.execute("select pipeline.upsert_booking(%s)", [Jsonb(payload)]).fetchone()
    result = row[0]
    return LoadResult(
        code=result["code"],
        message=result["message"],
        booking_id=result["booking_id"],
        review_id=result["review_id"],
    )
