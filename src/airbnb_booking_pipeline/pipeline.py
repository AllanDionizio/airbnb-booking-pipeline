"""
One run of the pipeline: email → parser → USD conversion → database.

Each email is handled on its own and ends in one `Outcome`, with a result
code. The database codes come from upsert_booking and register_cancellation;
the pipeline adds three of its own for emails that never reach the database:

  - UNRECOGNIZED: neither a confirmation nor a cancellation;
  - PARSE_ERROR: the parser rejected the email;
  - FX_ERROR: a USD amount and no rate to convert it.

A problem with one email never stops the run: the email is reported for a
person to look at and the next one goes on. A database error does stop it,
because it hits every email alike; rerunning is safe, since every email is
idempotent in the database.

Emails are processed oldest first, so when one booking has two emails in
the same run, the newer one is applied last and wins.
"""

import logging
import os
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .enrich import FxError, FxRate, convert_booking
from .load import CancellationResult, LoadResult
from .parsers import (
    BookingConfirmation,
    CancellationNotice,
    CancellationOutcome,
    parse_cancellation,
    parse_confirmation,
)
from .sources import Email

log = logging.getLogger(__name__)

# PTAX is published per Brazilian business day, so the day of an email, for
# the exchange rate, is its date in the business's time zone, not in UTC.
DEFAULT_BUSINESS_TZ = "America/Sao_Paulo"

# Routing is by subject, once the prefixes a forward adds ("Fwd:", "FW:",
# "Enc:") are dropped. A cancellation can also arrive without a subject, so
# its body heading counts too: on a line of its own, in capitals in Airbnb's
# text and in sentence case in the text Gmail writes for a forward.
_FORWARD_PREFIXES = re.compile(r"^(?:(?:fwd?|enc)\s*:\s*)+", re.IGNORECASE)
_CONFIRMATION_SUBJECT = "reserva confirmada"
_CANCELLATION_SUBJECT = "cancelado: reserva"
_CANCELLATION_HEADING = re.compile(r"^\s*(?:RESERVA CANCELADA|Reserva cancelada)\s*$", re.MULTILINE)


class Kind(StrEnum):
    BOOKING = "booking"
    CANCELLATION = "cancellation"
    UNRECOGNIZED = "unrecognized"


@dataclass(frozen=True)
class Outcome:
    message_id: str
    kind: Kind
    code: str
    message: str
    needs_attention: bool
    confirmation_code: str | None = None


@dataclass(frozen=True)
class RunReport:
    outcomes: list[Outcome]

    def counts(self) -> Counter[tuple[Kind, str]]:
        """How many emails ended in each (kind, code)."""
        return Counter((o.kind, o.code) for o in self.outcomes)

    @property
    def attention(self) -> list[Outcome]:
        """The emails a person has to look at."""
        return [o for o in self.outcomes if o.needs_attention]

    def to_dict(self) -> dict:
        """The summary the CLI prints, as JSON-ready data: totals per code and the attention list."""
        return {
            "processed": len(self.outcomes),
            "counts": [
                {"kind": kind, "code": code, "emails": n}
                for (kind, code), n in sorted(self.counts().items())
            ],
            "attention": [asdict(o) for o in self.attention],
        }


class Store(Protocol):
    """The database side, as load.PostgresStore implements it."""

    def upsert_booking(
        self, booking: BookingConfirmation, *, message_id: str, fx: FxRate | None = None
    ) -> LoadResult: ...

    def register_cancellation(
        self, notice: CancellationNotice, *, message_id: str, fx: FxRate | None = None
    ) -> CancellationResult: ...


class RateSource(Protocol):
    """USD → BRL rates, as enrich.PtaxClient implements it."""

    def usd_brl(self, day: date) -> FxRate: ...


def business_tz_from_env() -> ZoneInfo:
    """BUSINESS_TZ, or the default when unset; ValueError when it names no time zone."""
    name = os.environ.get("BUSINESS_TZ") or DEFAULT_BUSINESS_TZ
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f"BUSINESS_TZ is not a time zone: {name}") from None


def email_day(received_at: datetime, business_tz: ZoneInfo) -> date:
    """The calendar day an email arrived on, in the business's time zone."""
    return received_at.astimezone(business_tz).date()


def classify(email: Email) -> Kind:
    subject = _FORWARD_PREFIXES.sub("", (email.subject or "").strip()).lower()
    if subject.startswith(_CANCELLATION_SUBJECT):
        return Kind.CANCELLATION
    if subject.startswith(_CONFIRMATION_SUBJECT):
        return Kind.BOOKING
    if _CANCELLATION_HEADING.search(email.body):
        return Kind.CANCELLATION
    return Kind.UNRECOGNIZED


class Pipeline:
    def __init__(self, store: Store, rates: RateSource, business_tz: ZoneInfo | None = None):
        self.store = store
        self.rates = rates
        self.business_tz = business_tz or business_tz_from_env()

    def run(self, emails: Iterable[Email]) -> RunReport:
        ordered = sorted(emails, key=lambda email: email.received_at)
        return RunReport([self.process(email) for email in ordered])

    def process(self, email: Email) -> Outcome:
        kind = classify(email)
        if kind is Kind.BOOKING:
            outcome = self._booking(email)
        elif kind is Kind.CANCELLATION:
            outcome = self._cancellation(email)
        else:
            outcome = Outcome(
                email.message_id,
                kind,
                "UNRECOGNIZED",
                f"Not a booking confirmation or a cancellation: {email.subject!r}",
                needs_attention=True,
            )
        # A record, not an alarm: what needs a person is in the RunReport
        log.info(
            "%s %s %s %s: %s",
            outcome.message_id,
            outcome.kind,
            outcome.confirmation_code or "-",
            outcome.code,
            outcome.message,
        )
        return outcome

    def _rate_for(self, email: Email) -> FxRate:
        return self.rates.usd_brl(email_day(email.received_at, self.business_tz))

    def _booking(self, email: Email) -> Outcome:
        try:
            booking = parse_confirmation(
                email.body, subject=email.subject, email_date=email.received_at
            )
        except ValueError as exc:  # ParseError, or an impossible date
            return _parse_error(email, Kind.BOOKING, exc)

        fx = None
        if booking.currency == "USD":
            try:
                fx = self._rate_for(email)
            except FxError as exc:
                return _fx_error(email, Kind.BOOKING, booking.confirmation_code, exc)
            booking = convert_booking(booking, fx)

        result = self.store.upsert_booking(booking, message_id=email.message_id, fx=fx)
        return Outcome(
            email.message_id,
            Kind.BOOKING,
            result.code,
            result.message,
            needs_attention=result.needs_review or result.code == "INVALID_PAYLOAD",
            confirmation_code=booking.confirmation_code,
        )

    def _cancellation(self, email: Email) -> Outcome:
        try:
            notice = parse_cancellation(email.body, subject=email.subject)
        except ValueError as exc:
            return _parse_error(email, Kind.CANCELLATION, exc)

        fx = None
        if notice.currency == "USD":
            try:
                fx = self._rate_for(email)
            except FxError as exc:
                return _fx_error(email, Kind.CANCELLATION, notice.confirmation_code, exc)

        result = self.store.register_cancellation(notice, message_id=email.message_id, fx=fx)
        # An undetermined money outcome means the parser missed the sentence
        # that says whether there is a penalty to collect.
        undetermined = (
            notice.outcome is CancellationOutcome.UNDETERMINED
            and result.code != "ALREADY_PROCESSED"
        )
        return Outcome(
            email.message_id,
            Kind.CANCELLATION,
            result.code,
            result.message,
            needs_attention=(
                result.needs_attention or result.code == "INVALID_PAYLOAD" or undetermined
            ),
            confirmation_code=notice.confirmation_code,
        )


def _parse_error(email: Email, kind: Kind, exc: Exception) -> Outcome:
    return Outcome(email.message_id, kind, "PARSE_ERROR", str(exc), needs_attention=True)


def _fx_error(email: Email, kind: Kind, confirmation_code: str, exc: Exception) -> Outcome:
    return Outcome(
        email.message_id,
        kind,
        "FX_ERROR",
        str(exc),
        needs_attention=True,
        confirmation_code=confirmation_code,
    )
