"""Parsers for the Airbnb host emails (Portuguese-language templates)."""

from ._common import ParseError
from .cancellation import CancellationNotice, CancellationOutcome, parse_cancellation
from .confirmation import BookingConfirmation, parse_confirmation

__all__ = [
    "BookingConfirmation",
    "CancellationNotice",
    "CancellationOutcome",
    "ParseError",
    "parse_cancellation",
    "parse_confirmation",
]
