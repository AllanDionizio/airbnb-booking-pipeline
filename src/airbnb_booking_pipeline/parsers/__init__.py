"""Parsers for the Airbnb host emails (Portuguese-language templates)."""

from ._common import ParseError
from .confirmation import BookingConfirmation, parse_confirmation

__all__ = [
    "BookingConfirmation",
    "ParseError",
    "parse_confirmation",
]
