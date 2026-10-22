"""
Parser for Airbnb's "Reserva confirmada" email (host view).

Works on the text/plain part as it arrives straight from
automated@airbnb.com. That layout uses ALL-CAPS headings ("CÓDIGO DE
CONFIRMAÇÃO", "HÓSPEDES", "PAGAMENTO DO ANFITRIÃO", "VOCÊ RECEBE"), puts
check-in and checkout side by side in two columns, and writes each payout
line with the label and the amount on the SAME line.

The regex literals stay in Portuguese because they match the email text.

The guest name comes from the subject ("Reserva confirmada - <name> chega
em ..."): the body of the direct email does not carry it reliably.
"""

import re
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime

from ._common import CURRENCY, ParseError, currency_code, parse_amount

_MONTHS_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}  # fmt: skip

_RE_CONFIRMATION = re.compile(
    r"CÓDIGO DE CONFIRMAÇÃO\s*\n\s*([A-Z0-9]{8,})",
)
# The guest name comes from the subject header, not from the body.
_RE_GUEST = re.compile(
    r"Reserva confirmada\s*-\s*(.+?)\s+chega em",
)
# Two-column check-in/checkout block. The line with both dates sits between
# the "Check-in   Checkout" header and the times line ("15:00   12:00"). A
# date may or may not carry the year ("6 de set." vs "26 de dez. de 2026").
_RE_DATES_BLOCK = re.compile(
    r"Check-in\s+Checkout\s*\n(.*?)\n\s*\d{1,2}:\d{2}",
    re.DOTALL,
)
_RE_DATE = re.compile(
    r"(\d{1,2})\s+de\s+([a-zà-ÿ]+)\.?(?:\s+de\s+(\d{4}))?",
    re.IGNORECASE,
)
_RE_GUESTS = re.compile(
    r"HÓSPEDES\s*\n\s*\n\s*(\d+)\s+adultos?",
)
# Listing name: the line right after the /rooms/... URL and right before
# the property type ("Casa/apto inteiro").
_RE_LISTING = re.compile(
    r"/rooms/\d+\?[^\n]*\n\s*\n(.+?)\s*\n\s*\nCasa/apto",
)

# "PAGAMENTO DO ANFITRIÃO ... VOCÊ RECEBE  R$X,XX": the host's side of the
# money, which is what the booking records. The guest paid a different
# total (it includes Airbnb's guest fees), which does not matter here. The
# "\s*" between symbol and amount absorbs the nbsp (\xa0) of the USD format.
_RE_HOST_BLOCK = re.compile(
    r"PAGAMENTO DO ANFITRIÃO\s*\n(.*?)\nVOCÊ RECEBE\s+" + CURRENCY + r"\s*([\d.,]+)",
    re.DOTALL,
)
# One payout line: "<label>   <2+ spaces>   [-]<currency> <amount>".
_RE_PAYOUT_LINE = re.compile(r"^(.*?)\s{2,}(-)?" + CURRENCY + r"\s*([\d.,]+)$")

# Email label (lowercased) → payout field. Matched by prefix because Airbnb
# varies the suffix ("Preço da acomodação para 2 noites", "Taxa de serviço
# do anfitrião (4.0% + IVA)").
_LABEL_TO_FIELD: list[tuple[str, str]] = [
    ("preço da acomodação", "accommodation"),
    ("preço total da estadia", "accommodation"),
    ("taxa de limpeza", "cleaning_fee"),
    ("ajuste de preço por noite", "_nightly_price_adjustment"),  # the sign decides
    ("desconto", "discount"),  # monthly and long-stay discounts, stored as absolute values
    ("promoção", "discount"),  # new-listing promotion: a host discount, absolute value
    ("taxa de serviço do anfitrião", "host_service_fee"),
    ("taxa de animais de estimação", "pet_fee"),
]


@dataclass
class BookingConfirmation:
    confirmation_code: str
    guest_name: str
    check_in: date
    check_out: date
    num_guests: int | None
    listing_name: str | None
    currency: str  # "BRL" or "USD": the currency the amounts were extracted in
    payout_lines: dict[str, float]
    # In `currency`. Kept for auditing: the booking amounts come from payout_lines.
    host_payout: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["check_in"] = self.check_in.isoformat()
        d["check_out"] = self.check_out.isoformat()
        return d


def _month_to_num(month: str) -> int:
    key = month.lower().rstrip(".")[:3]
    try:
        return _MONTHS_PT[key]
    except KeyError as exc:
        raise ParseError(f"Unknown month: {month!r}") from exc


def _required(match: re.Match | None, field_name: str) -> re.Match:
    if match is None:
        raise ParseError(f"Could not extract the field {field_name!r}")
    return match


def _classify_label(label: str) -> str:
    """Return the payout field for an email label, or raise."""
    key = label.strip().lower()
    for prefix, field in _LABEL_TO_FIELD:
        if key.startswith(prefix):
            return field
    raise ParseError(f"Unknown label in the host payout block: {label!r}")


def _parse_host_block(body_text: str) -> tuple[dict[str, float], float, str]:
    """
    Extract the "PAGAMENTO DO ANFITRIÃO" → "VOCÊ RECEBE" block.

    Returns (payout_lines, host_payout, currency). payout_lines holds only
    the fields present in the email (a missing key means 0), in the original
    currency. Raises ParseError if the lines do not add up to host_payout or
    a label is unknown.
    """
    block_match = _required(_RE_HOST_BLOCK.search(body_text), "host_block")
    block_body = block_match.group(1)
    currency = currency_code(block_match.group(2))
    host_payout = parse_amount(block_match.group(3))

    lines: dict[str, float] = {}
    for raw in block_body.splitlines():
        line = raw.strip()
        if not line:
            continue
        money_match = _RE_PAYOUT_LINE.match(line)
        if not money_match:
            raise ParseError(f"Unexpected line in the host payout block: {line!r}")
        label = money_match.group(1).strip()
        is_negative = money_match.group(2) == "-"
        value = parse_amount(money_match.group(4))
        signed = -value if is_negative else value

        field = _classify_label(label)

        if field == "_nightly_price_adjustment":
            # The sign decides: a positive adjustment is extra income, a
            # negative one is a discount. Both are stored as absolute values.
            if signed >= 0:
                lines["other_income"] = lines.get("other_income", 0.0) + value
            else:
                lines["discount"] = lines.get("discount", 0.0) + value
        elif field in ("discount", "host_service_fee"):
            # Always absolute values: the database subtracts them.
            lines[field] = lines.get(field, 0.0) + value
        else:
            lines[field] = lines.get(field, 0.0) + signed

    # Consistency check: the lines, with the right signs, must add up to
    # "Você recebe" (1 cent tolerance).
    expected_payout = (
        lines.get("accommodation", 0.0)
        + lines.get("other_income", 0.0)
        + lines.get("cleaning_fee", 0.0)
        + lines.get("pet_fee", 0.0)
        - lines.get("discount", 0.0)
        - lines.get("host_service_fee", 0.0)
    )
    if abs(expected_payout - host_payout) > 0.01:
        raise ParseError(
            f"Payout lines add up to {expected_payout:.2f}, but 'Você recebe' "
            f"says {host_payout:.2f}. Lines: {lines}"
        )

    return lines, host_payout, currency


def _parse_date_token(match: re.Match, ref_year: int, ref_month: int) -> date:
    """Turn a _RE_DATE match into a date, inferring the year when it is missing."""
    day = int(match.group(1))
    month = _month_to_num(match.group(2))
    if match.group(3):
        year = int(match.group(3))
    else:
        year = ref_year if month >= ref_month else ref_year + 1
    return date(year, month, day)


def parse_confirmation(
    body_text: str,
    subject: str | None = None,
    email_date: datetime | None = None,
) -> BookingConfirmation:
    """
    Extract the booking fields from the text/plain body of the email.

    `subject` is the email subject, where the guest name comes from.
    `email_date` is used to infer the year when Airbnb leaves it out of the
    check-in/checkout dates. Default: now.
    """
    ref = (email_date or datetime.now(UTC)).date()

    code = _required(_RE_CONFIRMATION.search(body_text), "confirmation_code").group(1)
    guest = _required(_RE_GUEST.search(subject or ""), "guest_name").group(1).strip()

    dates_block = _required(_RE_DATES_BLOCK.search(body_text), "dates").group(1)
    date_matches = list(_RE_DATE.finditer(dates_block))
    if len(date_matches) < 2:
        raise ParseError(f"Expected 2 dates (check-in/checkout), found {len(date_matches)}")
    check_in = _parse_date_token(date_matches[0], ref.year, ref.month)
    check_out = _parse_date_token(date_matches[1], check_in.year, check_in.month)

    guests_match = _RE_GUESTS.search(body_text)
    num_guests = int(guests_match.group(1)) if guests_match else None

    listing_match = _RE_LISTING.search(body_text)
    listing = listing_match.group(1).strip() if listing_match else None

    lines, host_payout, currency = _parse_host_block(body_text)

    return BookingConfirmation(
        confirmation_code=code,
        guest_name=guest,
        check_in=check_in,
        check_out=check_out,
        num_guests=num_guests,
        listing_name=listing,
        currency=currency,
        payout_lines=lines,
        host_payout=host_payout,
    )
