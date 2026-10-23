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
"""

import textwrap
from dataclasses import dataclass
from datetime import date, datetime

from .listings import Listing

_WEEKDAYS_PT = ("seg.", "ter.", "qua.", "qui.", "sex.", "sáb.", "dom.")
_MONTHS_PT = (
    "jan.", "fev.", "mar.", "abr.", "mai.", "jun.",
    "jul.", "ago.", "set.", "out.", "nov.", "dez.",
)  # fmt: skip

# Airbnb writes date ranges with thin spaces around an en dash
_RANGE_SEP = "\u2009\u2013\u2009"
_WRAP = 60
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


def _wrap(paragraph: str) -> str:
    # Only ASCII whitespace breaks a line, so "R$\xa0287,15" stays whole,
    # as it does in the real emails.
    return textwrap.fill(paragraph, _WRAP, break_long_words=False, break_on_hyphens=False)


def _room_url(listing: Listing) -> str:
    return f"https://www.airbnb.com.br/rooms/{listing.room_id}?source_impression_id=email"


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
        _money_line(f"{money(accommodation / nights)} x {nights} noites", money(accommodation)),
        *(_money_line(label, money(amount)) for label, amount in repeated),
        _money_line("Taxa de serviço do hóspede", money(guest_fee)),
        _money_line(f"TOTAL ({spec.currency})", money(guest_total)),
    ]
    host_paid = [_money_line(label, money(amount)) for label, amount in spec.payout_lines]

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
            "\n\n".join(guest_paid),
            "",
            "PAGAMENTO DO ANFITRIÃO",
            "",
            "\n\n".join(host_paid),
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
    return RenderedEmail(
        subject=f"Reserva confirmada - {spec.guest} chega em {arrival}",
        body=body,
        email_date=spec.email_date,
    )


def render_cancellation(spec: CancellationSpec) -> RenderedEmail:
    year = spec.email_date.year
    stay = f"{_short_date(spec.check_in, year)}{_RANGE_SEP}{_short_date(spec.check_out, year)}"
    guests = f"{spec.num_guests} hóspede{'s' if spec.num_guests > 1 else ''}"

    paragraphs = [
        "RESERVA CANCELADA",
        f"{_room_url(spec.listing)}   {spec.listing.name}",
        f"Anúncio n° {spec.listing.room_id}",
        f"{stay}, {guests}",
        _wrap(
            f"Infelizmente, o seu hóspede, {spec.guest}, precisou cancelar a "
            f"reserva {spec.code} de {stay}. Seu calendário foi atualizado, e "
            "essas datas agora estão disponíveis."
        ),
    ]
    if spec.penalty is None:
        paragraphs.append(
            _wrap("Conforme sua política de cancelamento, o hóspede recebeu um reembolso integral.")
        )
    else:
        amount = format_money(spec.penalty, spec.currency, spec.money_sep)
        if spec.currency == "USD":
            amount += "\xa0USD"
        paragraphs += [
            _wrap(
                "Conforme sua política de cancelamento, seu recebimento foi "
                f"atualizado para {amount}."
            ),
            _wrap(
                "Este pagamento será liberado 24 horas após a data do check-in. "
                "É possível visualizar o pagamento atualizado em seu Histórico "
                "de Transações."
            ),
            _wrap(
                "Você também pode emitir o reembolso no valor total para o "
                "usuário clicando no botão abaixo."
            ),
            "Emitir reembolso integral",
        ]
    paragraphs.append(
        _wrap(
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
    return RenderedEmail(
        subject=subject, body="\n\n".join(paragraphs) + "\n", email_date=spec.email_date
    )
