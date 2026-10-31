"""
The demo inbox: data/sample_inbox/, a few months of a host's mailbox as .eml files.

Where the regression fixtures cover the layouts, the inbox tells a story
through the main paths of the pipeline. With the listings and contracts of
data/demo_seed.sql it produces:

  - bookings that load as drafts, in BRL and in USD;
  - a double booking on Casa Cedro (QUEUED_OVERLAP);
  - a December stay at Apartamento Duna 304, after its contract ends
    (QUEUED_NO_CONTRACT);
  - two bookings at Flat Fênix 606, which is not registered
    (QUEUED_UNKNOWN_LISTING), one of them cancelled later
    (FLAGGED_REVIEW_ITEM);
  - cancellations of a loaded booking (FLAGGED), one with a USD penalty,
    and of a booking older than the inbox (NOT_FOUND), sent without a subject;
  - a payout line the parser does not know (PARSE_ERROR), the way a new
    Airbnb template would first show up;
  - a guest message, which is neither email type (UNRECOGNIZED).

A second run adds ALREADY_PROCESSED. The other result codes (UPDATED,
ALREADY_EXISTS, ALREADY_CANCELLED, INVALID_PAYLOAD, FX_ERROR) are left to
the unit and integration tests.

Regenerate with `make inbox`; a test fails when the committed files drift.
"""

import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .emails import (
    CancellationSpec,
    ConfirmationSpec,
    RenderedEmail,
    render_cancellation,
    render_confirmation,
    to_eml,
)
from .listings import LISTINGS, Listing

AURORA, BOREAL, CEDRO, DUNA, ECLIPSE, FENIX = LISTINGS
_BRT = timezone(timedelta(hours=-3))
_SERVICE_FEE_RATE = 0.042


def _at(month: int, day: int, hour: int = 9, minute: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, tzinfo=_BRT)


def _booking(
    code: str,
    guest: str,
    listing: Listing,
    check_in: date,
    nights: int,
    nightly: float,
    cleaning: float,
    email_date: datetime,
    *,
    extra_lines: tuple[tuple[str, float], ...] = (),
    adults: int = 2,
    currency: str = "BRL",
    money_sep: str = "",
) -> ConfirmationSpec:
    """A confirmation whose host service fee is a flat share of the gross."""
    accommodation = round(nightly * nights, 2)
    gross = accommodation + cleaning + sum(amount for _, amount in extra_lines)
    fee = round(gross * _SERVICE_FEE_RATE, 2)
    return ConfirmationSpec(
        code=code,
        guest=guest,
        listing=listing,
        check_in=check_in,
        check_out=check_in + timedelta(days=nights),
        email_date=email_date,
        adults=adults,
        other_guests=None,
        payout_lines=(
            (f"Preço da acomodação para {nights} noites", accommodation),
            ("Taxa de limpeza", cleaning),
            *extra_lines,
            (f"Taxa de serviço do anfitrião ({_SERVICE_FEE_RATE * 100:.1f}% + IVA)", -fee),
        ),
        currency=currency,
        money_sep=money_sep,
    )


def _cancellation_of(
    booking: ConfirmationSpec, email_date: datetime, penalty: float | None = None
) -> CancellationSpec:
    return CancellationSpec(
        code=booking.code,
        guest=booking.guest,
        listing=booking.listing,
        check_in=booking.check_in,
        check_out=booking.check_out,
        email_date=email_date,
        num_guests=booking.adults,
        penalty=penalty,
        currency=booking.currency,
        money_sep=booking.money_sep,
    )


# --- Bookings --------------------------------------------------------------------

_AURORA_SEP = _booking(
    "HMTEST3002", "Guest Bravo", AURORA, date(2026, 9, 4), 2, 279, 85, _at(8, 20)
)
_ECLIPSE_OCT = _booking(
    "HMTEST3014", "Guest Oscar", ECLIPSE, date(2026, 10, 2), 3, 104, 35, _at(9, 10),
    currency="USD", money_sep=" ",
)  # fmt: skip
_FENIX_SEP = _booking("HMTEST3015", "Guest Papa", FENIX, date(2026, 9, 5), 7, 191, 125, _at(8, 18))

BOOKINGS: tuple[ConfirmationSpec, ...] = (
    _booking("HMTEST3001", "Guest Alpha", AURORA, date(2026, 8, 14), 3, 263, 85, _at(8, 2)),
    _AURORA_SEP,
    _booking("HMTEST3003", "Guest Charlie", AURORA, date(2026, 10, 9), 3, 312, 85, _at(9, 15)),
    _booking(
        "HMTEST3004", "Guest Delta", BOREAL, date(2026, 8, 21), 3, 214, 75, _at(8, 5),
        money_sep=" ",
    ),
    _booking(
        "HMTEST3005", "Guest Echo", BOREAL, date(2026, 9, 18), 28, 168, 115, _at(8, 30, 14),
        extra_lines=(("Desconto mensal", -952.00),),
    ),
    _booking(
        "HMTEST3006", "Guest Foxtrot", BOREAL, date(2026, 10, 23), 2, 236, 75, _at(10, 6),
        extra_lines=(("Taxa de animais de estimação", 55.00),),
    ),
    _booking(
        "HMTEST3007", "Guest Golf", CEDRO, date(2026, 8, 7), 3, 394, 145, _at(8, 1),
        adults=4, money_sep="\xa0",
    ),
    _booking("HMTEST3008", "Guest Hotel", CEDRO, date(2026, 9, 11), 3, 417, 145, _at(8, 25)),
    # The same nights as HMTEST3008: a double booking, for a person to sort out
    _booking("HMTEST3009", "Guest India", CEDRO, date(2026, 9, 12), 3, 403, 145, _at(8, 26)),
    _booking(
        "HMTEST3010", "Guest Juliett", CEDRO, date(2026, 12, 28), 5, 527, 145, _at(10, 12),
        adults=5,
    ),
    _booking("HMTEST3011", "Guest Kilo", DUNA, date(2026, 9, 25), 3, 243, 105, _at(9, 2)),
    # After the end of the Duna contract
    _booking("HMTEST3012", "Guest Lima", DUNA, date(2026, 12, 19), 3, 261, 105, _at(10, 14)),
    _booking(
        "HMTEST3013", "Guest Mike", ECLIPSE, date(2026, 8, 28), 3, 97, 35, _at(8, 10),
        currency="USD", money_sep="\xa0",
    ),
    _ECLIPSE_OCT,
    # Flat Fênix 606 is not registered
    _FENIX_SEP,
    _booking("HMTEST3016", "Guest Quebec", FENIX, date(2026, 10, 15), 5, 213, 125, _at(9, 28)),
    # A payout line the parser has never seen
    _booking(
        "HMTEST3017", "Guest Romeo", AURORA, date(2026, 11, 6), 2, 306, 85, _at(10, 3),
        extra_lines=(("Crédito de compensação do Airbnb", 20.00),),
    ),
)  # fmt: skip

# --- Cancellations ---------------------------------------------------------------

CANCELLATIONS: tuple[CancellationSpec, ...] = (
    _cancellation_of(_AURORA_SEP, _at(8, 28, 11, 20)),
    _cancellation_of(_FENIX_SEP, _at(8, 27, 16, 45)),
    _cancellation_of(_ECLIPSE_OCT, _at(9, 22, 19, 10), penalty=156.00),
    # A booking from before the inbox starts, and an email without a subject
    CancellationSpec(
        code="HMTEST3099",
        guest="Guest Sierra",
        listing=DUNA,
        check_in=date(2026, 8, 15),
        check_out=date(2026, 8, 18),
        email_date=_at(8, 12, 8, 30),
        num_guests=2,
        penalty=327.00,
        with_subject=False,
    ),
)

# A guest message: the pipeline must report it, not guess
_GUEST_MESSAGE = RenderedEmail(
    subject="Nova mensagem de Guest Kilo",
    body=(
        "Guest Kilo enviou uma mensagem sobre a reserva no Apartamento Duna 304.\n"
        "\n"
        "Olá! Podemos fazer o check-in um pouco mais cedo?\n"
    ),
    email_date=_at(9, 20, 10, 15),
)


def build_inbox() -> dict[str, bytes]:
    """File name → .eml bytes, named by arrival so a directory listing reads in order."""
    emails = [(render_confirmation(spec), spec.code, "confirmation") for spec in BOOKINGS] + [
        (render_cancellation(spec), spec.code, "cancellation") for spec in CANCELLATIONS
    ]
    emails.append((_GUEST_MESSAGE, "message", "guest"))

    files = {}
    for email, code, kind in emails:
        name = f"{email.email_date:%Y-%m-%d_%H%M}_{code}_{kind}.eml"
        files[name] = to_eml(email, f"<{code}.{kind}@example.invalid>")
    return files


def write_inbox(root: Path) -> list[Path]:
    """Write every email and delete the files no longer generated."""
    files = build_inbox()
    root.mkdir(parents=True, exist_ok=True)
    for path in root.glob("*.eml"):
        if path.name not in files:
            path.unlink()
    written = []
    for name, contents in sorted(files.items()):
        path = root / name
        path.write_bytes(contents)
        written.append(path)
    return written


if __name__ == "__main__":
    for path in write_inbox(Path(sys.argv[1] if len(sys.argv) > 1 else "data/sample_inbox")):
        print(path)
