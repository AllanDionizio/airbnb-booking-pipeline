"""
The one module that knows the database contract.

Bookings go in through pipeline.upsert_booking(jsonb) and cancellations
through pipeline.register_cancellation(jsonb) (see sql/). The functions own
idempotency, quarantine and matching; this module only builds their payloads
and reads their answers, so the rules live in one place and every client of
the database gets them.
"""

from dataclasses import dataclass

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from ..enrich import FxRate
from ..parsers import BookingConfirmation, CancellationNotice


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


@dataclass(frozen=True)
class CancellationResult:
    # FLAGGED, FLAGGED_REVIEW_ITEM, ALREADY_CANCELLED, NOT_FOUND, ALREADY_PROCESSED,
    # INVALID_PAYLOAD
    code: str
    message: str
    event_id: int | None = None
    booking_id: int | None = None
    review_id: int | None = None

    @property
    def needs_attention(self) -> bool:
        """True when a person has to confirm the cancellation or find its booking."""
        return self.code in ("FLAGGED", "FLAGGED_REVIEW_ITEM", "NOT_FOUND")


def _fx_payload(fx: FxRate | None) -> dict | None:
    if fx is None:
        return None
    return {"rate": fx.rate, "quoted_on": fx.quoted_on.isoformat(), "source": fx.source}


def _call(conn: psycopg.Connection, function: str, payload: dict) -> dict:
    """Run one pipeline.<function>(jsonb) call in its own transaction."""
    # On a connection with a transaction already open, conn.transaction()
    # would only open a savepoint, and nothing would be committed until the
    # caller committed.
    if not conn.autocommit:
        raise ValueError("Use an autocommit connection: each email is committed on its own")
    with conn.transaction():
        query = sql.SQL("select pipeline.{}(%s)").format(sql.Identifier(function))
        row = conn.execute(query, [Jsonb(payload)]).fetchone()
    return row[0]


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
        "fx": _fx_payload(fx),
    }


def upsert_booking(
    conn: psycopg.Connection,
    booking: BookingConfirmation,
    *,
    message_id: str,
    fx: FxRate | None = None,
) -> LoadResult:
    """
    Load one booking in its own transaction and return what the database did.

    `conn` must be in autocommit mode.
    """
    result = _call(conn, "upsert_booking", booking_payload(booking, message_id=message_id, fx=fx))
    return LoadResult(
        code=result["code"],
        message=result["message"],
        booking_id=result["booking_id"],
        review_id=result["review_id"],
    )


def cancellation_payload(
    notice: CancellationNotice,
    *,
    message_id: str,
    fx: FxRate | None = None,
    platform: str = "airbnb",
) -> dict:
    """
    Build the register_cancellation payload.

    The penalty goes as the email states it; for a USD penalty pass `fx`, and
    the database stores both amounts and the rate.
    """
    if notice.currency == "USD" and fx is None:
        raise ValueError("A USD penalty needs the rate to convert it")
    return {
        "message_id": message_id,
        "platform": platform,
        "confirmation_code": notice.confirmation_code,
        "outcome": notice.outcome.value,
        "penalty_amount": notice.penalty_amount,
        "currency": notice.currency,
        "fx": _fx_payload(fx) if notice.currency == "USD" else None,
    }


def register_cancellation(
    conn: psycopg.Connection,
    notice: CancellationNotice,
    *,
    message_id: str,
    fx: FxRate | None = None,
) -> CancellationResult:
    """
    Record one cancellation email in its own transaction.

    Nothing gets cancelled: the booking is flagged for a person to confirm.
    `conn` must be in autocommit mode.
    """
    payload = cancellation_payload(notice, message_id=message_id, fx=fx)
    result = _call(conn, "register_cancellation", payload)
    return CancellationResult(
        code=result["code"],
        message=result["message"],
        event_id=result["event_id"],
        booking_id=result["booking_id"],
        review_id=result["review_id"],
    )
