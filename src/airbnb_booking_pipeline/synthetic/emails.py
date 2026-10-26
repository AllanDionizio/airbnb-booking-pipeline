"""
Render synthetic "Reserva confirmada" and "Cancelado" emails from a spec.

The output mimics the text/plain part of Airbnb's Portuguese host emails,
including the quirks the parsers have to survive:

  - the confirmation puts check-in and checkout side by side in two columns;
  - it carries a guest-side money block ("O HÓSPEDE PAGOU") with labels
    that also appear in the host block, so the parser must anchor on
    "PAGAMENTO DO ANFITRIÃO";
  - amounts come in three spellings: "R$517,30", "R$ 517,30" and
    "R$\\xa0517,30" (nbsp), and USD uses "$" with the Brazilian number format;
  - the cancellation prose is wrapped at ~60 columns, so sentences break
    across lines;
  - a cancellation with a penalty also shows an "Emitir reembolso integral"
    button, the trap behind the full-refund false positive.

A spec with `forwarded=True` renders the email as Gmail forwards it. Gmail
does not keep the original text/plain part: it writes a new one from the
HTML, so a forward is a different layout, not the same body under a header.
Headings lose their capitals ("Código de confirmação"), check-in and checkout
are stacked instead of side by side, every payout label sits on its own line
above its amount, links show up in angle brackets, prose wraps at ~76
columns and lines end in CRLF. On top come a "Forwarded message" block and
"Fwd:" in the subject.
"""

import textwrap
from dataclasses import dataclass
from datetime import date, datetime
from email.message import EmailMessage
from email.utils import format_datetime

from .listings import Listing

_WEEKDAYS_PT = ("seg.", "ter.", "qua.", "qui.", "sex.", "sáb.", "dom.")
_MONTHS_PT = (
    "jan.", "fev.", "mar.", "abr.", "mai.", "jun.",
    "jul.", "ago.", "set.", "out.", "nov.", "dez.",
)  # fmt: skip

# Airbnb writes date ranges with thin spaces around an en dash
_RANGE_SEP = "\u2009\u2013\u2009"
_WRAP = 60
_FORWARD_WRAP = 76
_COLUMN = 19  # width of the check-in column in the two-column block
_GUEST_SERVICE_RATE = 0.14


@dataclass(frozen=True)
class RenderedEmail:
    subject: str | None
    body: str
    email_date: datetime


@dataclass(frozen=True)
class ConfirmationSpec:
    code: str
    guest: str
    listing: Listing
    check_in: date
    check_out: date
    email_date: datetime
    adults: int
    # Line after the adults, as Airbnb writes it ("1 criança", "1 bebê")
    other_guests: str | None
    # (label, signed amount) in `currency`, in the order the email lists them.
    # The first line is the accommodation price.
    payout_lines: tuple[tuple[str, float], ...]
    currency: str = "BRL"
    # Between symbol and amount: "", " " or "\xa0"
    money_sep: str = ""
    forwarded: bool = False

    @property
    def host_payout(self) -> float:
        return round(sum(amount for _, amount in self.payout_lines), 2)


@dataclass(frozen=True)
class CancellationSpec:
    code: str
    guest: str
    listing: Listing
    check_in: date
    check_out: date
    email_date: datetime
    num_guests: int
    penalty: float | None  # None means the guest got a full refund
    currency: str = "BRL"
    money_sep: str = ""
    with_subject: bool = True
    forwarded: bool = False


def format_money(value: float, currency: str = "BRL", sep: str = "") -> str:
    """1234.5 -> 'R$1.234,50'; -7.4 in USD with sep='\\xa0' -> '-$\\xa07,40'."""
    symbol = "R$" if currency == "BRL" else "$"
    sign = "-" if value < 0 else ""
    digits = f"{abs(value):,.2f}".translate(str.maketrans(",.", ".,"))
    return f"{sign}{symbol}{sep}{digits}"


def _short_date(day: date, ref_year: int) -> str:
    """'15 de mai.', with ' de <year>' only when the year differs from the email's."""
    text = f"{day.day} de {_MONTHS_PT[day.month - 1]}"
    return text if day.year == ref_year else f"{text} de {day.year}"


def _long_date(day: date, ref_year: int) -> str:
    """'sex., 15 de mai.'"""
    return f"{_WEEKDAYS_PT[day.weekday()]}, {_short_date(day, ref_year)}"


def _two_columns(left: str, right: str) -> str:
    return left.ljust(max(_COLUMN, len(left) + 3)) + right


def _money_line(label: str, amount: str) -> str:
    return f"{label}   {amount}"


def _stacked(pairs: list[tuple[str, str]]) -> list[str]:
    """Each label above its amount, with blank lines in between, as a forward lays them out."""
    lines = []
    for label, amount in pairs:
        lines += [label, "", amount, ""]
    return lines[:-1]


def _wrap(paragraph: str, width: int = _WRAP) -> str:
    # Only ASCII whitespace breaks a line, so "R$\xa0287,15" stays whole,
    # as it does in the real emails.
    return textwrap.fill(paragraph, width, break_long_words=False, break_on_hyphens=False)


def _room_url(listing: Listing) -> str:
    return f"https://www.airbnb.com.br/rooms/{listing.room_id}?source_impression_id=email"


def _reservation_url(code: str) -> str:
    return f"https://www.airbnb.com.br/hosting/reservations/details/{code}"


def _forward(subject: str | None, sent: datetime, lines: list[str]) -> RenderedEmail:
    """`lines`, the body Gmail writes for a forward, under its "Forwarded message" block."""
    header = [
        "---------- Forwarded message ---------",
        "De: Airbnb <automated@airbnb.com>",
        f"Date: {_WEEKDAYS_PT[sent.weekday()]}, {sent.day} de {_MONTHS_PT[sent.month - 1]} "
        f"de {sent.year} às {sent:%H:%M}",
        f"Subject: {subject or ''}",
        "To: <host@example.com>",
        "",
        "",
        # The preheader Airbnb pads with invisible characters
        "\u2007\u034f " * 12,
        "[image: Airbnb]",
        "<https://www.airbnb.com.br/>",
        "",
    ]
    return RenderedEmail(
        subject=f"Fwd: {subject or ''}",
        body="\r\n".join(header + lines) + "\r\n",
        email_date=sent,
    )


def render_confirmation(spec: ConfirmationSpec) -> RenderedEmail:
    def money(value: float) -> str:
        return format_money(value, spec.currency, spec.money_sep)

    year = spec.email_date.year
    nights = (spec.check_out - spec.check_in).days
    arrival = _short_date(spec.check_in, year)

    guests = f"{spec.adults} adulto{'s' if spec.adults > 1 else ''}"
    if spec.other_guests:
        guests += f", {spec.other_guests}"

    # The guest-side block repeats the cleaning and pet fees and adds the
    # guest service fee. The parser must ignore all of it.
    accommodation = spec.payout_lines[0][1]
    repeated = [
        (label, amount)
        for label, amount in spec.payout_lines
        if label.startswith(("Taxa de limpeza", "Taxa de animais"))
    ]
    guest_fee = round(accommodation * _GUEST_SERVICE_RATE, 2)
    guest_total = round(accommodation + sum(a for _, a in repeated) + guest_fee, 2)
    guest_paid = [
        (f"{money(accommodation / nights)} x {nights} noites", money(accommodation)),
        *((label, money(amount)) for label, amount in repeated),
        ("Taxa de serviço do hóspede", money(guest_fee)),
    ]
    host_paid = [(label, money(amount)) for label, amount in spec.payout_lines]

    subject = f"Reserva confirmada - {spec.guest} chega em {arrival}"
    if spec.forwarded:
        lines = [
            f"Nova reserva confirmada! {spec.guest} chega em {arrival}.",
            "",
            _wrap(
                "Envie uma mensagem para confirmar os detalhes do check-in "
                "ou para dar as boas-vindas.",
                _FORWARD_WRAP,
            ),
            f"<{_reservation_url(spec.code)}>",
            "",
            f"[image: {spec.listing.name}]",
            f"<{_room_url(spec.listing)}>",
            "",
            spec.listing.name,
            "",
            "Casa/apto inteiro",
            f"<{_room_url(spec.listing)}>",
            "",
            "Check-in",
            "",
            _long_date(spec.check_in, year),
            "",
            "15:00",
            "",
            "Checkout",
            "",
            _long_date(spec.check_out, year),
            "",
            "11:00",
            "Hóspedes",
            "",
            guests,
            "Código de confirmação",
            "",
            spec.code,
            "",
            "O hóspede pagou",
            "",
            *_stacked(guest_paid),
            f"Total ({spec.currency})",
            money(guest_total),
            "Pagamento do anfitrião",
            "",
            *_stacked(host_paid),
            "Você recebe",
            money(spec.host_payout),
            "",
            _wrap("O processamento do pagamento pode levar algum tempo.", _FORWARD_WRAP),
        ]
        return _forward(subject, spec.email_date, lines)

    blank = " " * _COLUMN
    body = "\n".join(
        [
            f"Nova reserva confirmada! {spec.guest} chega em {arrival}",
            "",
            _wrap(
                "Envie uma mensagem para confirmar os detalhes do check-in "
                "ou para dar as boas-vindas."
            ),
            "",
            _room_url(spec.listing),
            "",
            spec.listing.name,
            "",
            "Casa/apto inteiro",
            "",
            _two_columns("Check-in", "Checkout"),
            blank,
            _two_columns(_long_date(spec.check_in, year), _long_date(spec.check_out, year)),
            blank,
            _two_columns("15:00", "11:00"),
            "",
            "HÓSPEDES",
            "",
            guests,
            "",
            "CÓDIGO DE CONFIRMAÇÃO",
            spec.code,
            "",
            "O HÓSPEDE PAGOU",
            "",
            "\n\n".join(_money_line(label, amount) for label, amount in guest_paid),
            "",
            _money_line(f"TOTAL ({spec.currency})", money(guest_total)),
            "",
            "PAGAMENTO DO ANFITRIÃO",
            "",
            "\n\n".join(_money_line(label, amount) for label, amount in host_paid),
            "",
            _money_line("VOCÊ RECEBE", money(spec.host_payout)),
            "",
            _wrap("O processamento do pagamento pode levar algum tempo."),
            "",
            "CANCELAMENTOS",
            "Sua política de cancelamento para os hóspedes é Moderada",
            "",
        ]
    )
    return RenderedEmail(subject=subject, body=body, email_date=spec.email_date)


def render_cancellation(spec: CancellationSpec) -> RenderedEmail:
    year = spec.email_date.year
    stay = f"{_short_date(spec.check_in, year)}{_RANGE_SEP}{_short_date(spec.check_out, year)}"
    guests = f"{spec.num_guests} hóspede{'s' if spec.num_guests > 1 else ''}"
    width = _FORWARD_WRAP if spec.forwarded else _WRAP

    def wrap(paragraph: str) -> str:
        return _wrap(paragraph, width)

    if spec.forwarded:
        top = [
            f"Reserva cancelada\n[image: {spec.listing.name}]\n<{_reservation_url(spec.code)}>",
            f"{spec.listing.name}\n<{_reservation_url(spec.code)}>",
        ]
    else:
        top = ["RESERVA CANCELADA", f"{_room_url(spec.listing)}   {spec.listing.name}"]
    paragraphs = [
        *top,
        f"Anúncio n° {spec.listing.room_id}",
        f"{stay}, {guests}",
        wrap(
            f"Infelizmente, o seu hóspede, {spec.guest}, precisou cancelar a "
            f"reserva {spec.code} de {stay}. Seu calendário foi atualizado, e "
            "essas datas agora estão disponíveis."
        ),
    ]
    if spec.penalty is None:
        paragraphs.append(
            wrap("Conforme sua política de cancelamento, o hóspede recebeu um reembolso integral.")
        )
    else:
        amount = format_money(spec.penalty, spec.currency, spec.money_sep)
        if spec.currency == "USD":
            amount += "\xa0USD"
        paragraphs += [
            wrap(
                "Conforme sua política de cancelamento, seu recebimento foi "
                f"atualizado para {amount}."
            ),
            wrap(
                "Este pagamento será liberado 24 horas após a data do check-in. "
                "É possível visualizar o pagamento atualizado em seu Histórico "
                "de Transações."
            ),
            wrap(
                "Você também pode emitir o reembolso no valor total para o "
                "usuário clicando no botão abaixo."
            ),
            "Emitir reembolso integral",
        ]
    paragraphs.append(
        wrap(
            "Tem alguma dúvida? Confira mais informações sobre como funciona "
            "quando um hóspede cancela."
        )
    )

    subject = None
    if spec.with_subject:
        # The subject always closes the range with the checkout year
        end_year = spec.check_out.year
        dated_stay = (
            f"{_short_date(spec.check_in, end_year)}{_RANGE_SEP}"
            f"{_short_date(spec.check_out, end_year)} de {end_year}"
        )
        subject = f"Cancelado: reserva {spec.code} de {dated_stay}"
    if spec.forwarded:
        return _forward(subject, spec.email_date, "\n\n".join(paragraphs).split("\n"))
    return RenderedEmail(
        subject=subject, body="\n\n".join(paragraphs) + "\n", email_date=spec.email_date
    )


def to_eml(email: RenderedEmail, message_id: str) -> bytes:
    """Wrap a rendered email in an RFC 822 message, as a mail client would save it."""
    msg = EmailMessage()
    msg["From"] = "Airbnb <automated@airbnb.com>"
    msg["To"] = "host@example.com"
    if email.subject is not None:
        msg["Subject"] = email.subject
    msg["Date"] = format_datetime(email.email_date)
    msg["Message-ID"] = message_id
    if "\r" in email.body:
        # A forward's CRLF lines survive only as encoded bytes: set_content
        # on the text would turn them into LF.
        msg.set_content(
            email.body.encode(), "text", "plain", cte="base64", params={"charset": "utf-8"}
        )
    else:
        msg.set_content(email.body)
    return msg.as_bytes()
