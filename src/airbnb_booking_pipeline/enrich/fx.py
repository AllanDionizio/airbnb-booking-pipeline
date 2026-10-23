"""
USD → BRL conversion for bookings priced in dollars.

Bookings are stored in BRL, so a USD booking is converted before it is
loaded. The rate is the Banco Central do Brasil's PTAX sell rate ("cotação
de venda"), from the public Olinda API (no key needed).

The converted amounts are an approximation on purpose: the exact BRL amount
only exists in the payout email Airbnb sends days after check-in, and is
reconciled by hand.

Network access lives here so the parsers stay pure and testable offline.
"""

import logging
import os
from dataclasses import dataclass, replace
from datetime import date, timedelta

import httpx

from ..parsers import BookingConfirmation

log = logging.getLogger(__name__)

# The period endpoint, not the single-day one: it returns every quote in a
# window, so a weekend, a holiday or a PTAX not yet published today all
# resolve to the latest earlier quote.
_PTAX_URL = (
    "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/"
    "CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
)
_LOOKBACK_DAYS = 10  # a weekend plus a long holiday
_TIMEOUT = 10.0


class FxError(RuntimeError):
    """PTAX is unavailable and there is no fallback rate to use instead."""


@dataclass(frozen=True)
class FxRate:
    rate: float  # BRL per USD
    quoted_on: date  # may be earlier than the requested day (weekend, holiday)
    source: str  # "ptax" or "fallback"


class PtaxClient:
    """
    USD → BRL rates from PTAX, cached per day for the life of the client.

    When PTAX fails (network, HTTP error, empty window) the client returns
    `fallback_rate` if one was given, and raises FxError otherwise: a booking
    that stops for lack of a rate is visible, one loaded with a made-up rate
    is not.
    """

    def __init__(self, http: httpx.Client | None = None, fallback_rate: float | None = None):
        self._http = http or httpx.Client(timeout=_TIMEOUT)
        self._fallback_rate = fallback_rate
        self._cache: dict[date, FxRate] = {}

    @classmethod
    def from_env(cls, http: httpx.Client | None = None) -> "PtaxClient":
        """Take the fallback rate from USD_BRL_FALLBACK ('5,43' or '5.43'), when set."""
        raw = os.environ.get("USD_BRL_FALLBACK")
        return cls(http, fallback_rate=float(raw.replace(",", ".")) if raw else None)

    def usd_brl(self, day: date) -> FxRate:
        """The latest PTAX sell rate published on or before `day`."""
        if day in self._cache:
            return self._cache[day]
        try:
            fx = self._fetch(day)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            return self._fallback(day, exc)
        self._cache[day] = fx
        return fx

    def _fetch(self, day: date) -> FxRate:
        start = day - timedelta(days=_LOOKBACK_DAYS)
        response = self._http.get(
            _PTAX_URL,
            params={
                "@dataInicial": f"'{start:%m-%d-%Y}'",  # OData wants quoted MM-DD-YYYY
                "@dataFinalCotacao": f"'{day:%m-%d-%Y}'",
                "$format": "json",
            },
        )
        response.raise_for_status()
        quotes = response.json()["value"]
        if not quotes:
            raise ValueError(f"PTAX has no quote between {start} and {day}")

        # "dataHoraCotacao" is "YYYY-MM-DD HH:MM:SS.ffffff", so the string
        # order is the time order.
        latest = max(quotes, key=lambda quote: quote["dataHoraCotacao"])
        fx = FxRate(
            rate=float(latest["cotacaoVenda"]),
            quoted_on=date.fromisoformat(latest["dataHoraCotacao"][:10]),
            source="ptax",
        )
        log.info("PTAX sell rate %.4f quoted on %s (asked for %s)", fx.rate, fx.quoted_on, day)
        return fx

    def _fallback(self, day: date, exc: Exception) -> FxRate:
        if self._fallback_rate is None:
            raise FxError(f"PTAX unavailable for {day} and no fallback rate is set") from exc
        log.warning(
            "PTAX failed (%s: %s); using the fallback rate %.4f for %s",
            type(exc).__name__,
            exc,
            self._fallback_rate,
            day,
        )
        # Not cached: if PTAX comes back, the next call for the same day gets
        # the real rate instead of repeating the emergency one.
        return FxRate(rate=self._fallback_rate, quoted_on=day, source="fallback")


def convert_booking(booking: BookingConfirmation, fx: FxRate) -> BookingConfirmation:
    """
    Return a copy of a USD booking with every amount in BRL.

    Each payout line and the host payout are multiplied by the rate and
    rounded to cents separately, so the lines keep the proportions of the
    email. The original booking is left untouched.
    """
    if booking.currency != "USD":
        raise ValueError(f"Only USD bookings are converted, got {booking.currency}")
    return replace(
        booking,
        currency="BRL",
        payout_lines={field: round(v * fx.rate, 2) for field, v in booking.payout_lines.items()},
        host_payout=round(booking.host_payout * fx.rate, 2),
    )
