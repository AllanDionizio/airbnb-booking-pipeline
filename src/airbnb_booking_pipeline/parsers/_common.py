"""Helpers shared by the confirmation and cancellation parsers."""

import re


class ParseError(ValueError):
    """The email does not have the layout the parser expects."""


# Currency symbol: "R$" (Brazilian real) or "$"/"US$" (US dollar, used by
# USD-priced listings). Order matters in the alternation: "R$" must be tried
# BEFORE "$", or the "$" inside "R$" matches first.
CURRENCY = r"(R\$|US\$|\$)"


def parse_amount(text: str) -> float:
    """'1.234,56' -> 1234.56. Airbnb uses the Brazilian number format for BRL and USD alike."""
    return float(text.replace(".", "").replace(",", "."))


def currency_code(symbol: str) -> str:
    """'R$' -> 'BRL'; '$' or 'US$' -> 'USD'."""
    return "BRL" if symbol.startswith("R") else "USD"


def flatten(text: str | None) -> str:
    """Collapse line breaks and runs of whitespace (nbsp included) into one space."""
    return re.sub(r"\s+", " ", text or "")
