"""The email the pipeline works on, and the contract every source follows."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from typing import Protocol


@dataclass(frozen=True)
class Email:
    # The idempotency key in the database: the same email must always carry
    # the same id, whichever source it came from.
    message_id: str
    subject: str | None
    body: str  # the text/plain part, which is what the parsers read
    received_at: datetime  # timezone-aware


class Source(Protocol):
    def fetch(self) -> Iterable[Email]: ...


def from_rfc822(raw: bytes, *, default_id: str, default_date: datetime | None = None) -> Email:
    """
    Read one RFC 822 message (an .eml file, or Gmail's "raw" format).

    The Message-ID header is the id; `default_id` stands in when it is
    missing. The Date header is when the email arrived; `default_date` stands
    in when it is missing or unreadable, and without one that is an error. A
    Date without a time zone is taken as UTC.
    """
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    # An unparseable Date header comes back as a header whose datetime is None
    received_at = msg["Date"].datetime if msg["Date"] is not None else None
    if received_at is None:
        received_at = default_date
    if received_at is None:
        raise ValueError(f"{default_id}: the email has no valid Date header")
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)

    part = msg.get_body(preferencelist=("plain",))
    return Email(
        message_id=str(msg["Message-ID"] or "").strip() or default_id,
        subject=str(msg["Subject"]) if msg["Subject"] is not None else None,
        body=part.get_content() if part is not None else "",
        received_at=received_at,
    )
