"""Hand-written cases for the confirmation parser.

The bodies keep only the parts of the real template the parser reads. The
full synthetic regression fixtures come with the email generator.
"""

from datetime import date, datetime

import pytest

from airbnb_booking_pipeline.parsers import ParseError, parse_confirmation

SUBJECT = "Reserva confirmada - Guest Alpha chega em 15 de mai."
EMAIL_DATE = datetime(2026, 5, 2)


def make_body(
    dates: str = "sex., 15 de mai.   dom., 17 de mai.",
    payout_lines: str = (
        "Preço da acomodação para 2 noites   R$517,30\n\n"
        "Taxa de limpeza   R$93,70\n\n"
        "Taxa de serviço do anfitrião (4.0% + IVA)   -R$24,45"
    ),
    you_receive: str = "R$586,55",
) -> str:
    return f"""https://www.airbnb.com.br/rooms/1000?c=x

TEST LISTING ONE

Casa/apto inteiro

Check-in           Checkout

{dates}

15:00              11:00

HÓSPEDES

3 adultos, 1 criança

CÓDIGO DE CONFIRMAÇÃO
HMTEST0001

PAGAMENTO DO ANFITRIÃO

{payout_lines}

VOCÊ RECEBE   {you_receive}
"""


def test_extracts_every_field():
    booking = parse_confirmation(make_body(), subject=SUBJECT, email_date=EMAIL_DATE)

    assert booking.to_dict() == {
        "confirmation_code": "HMTEST0001",
        "guest_name": "Guest Alpha",
        "check_in": "2026-05-15",
        "check_out": "2026-05-17",
        "num_guests": 3,
        "listing_name": "TEST LISTING ONE",
        "currency": "BRL",
        "payout_lines": {"accommodation": 517.30, "cleaning_fee": 93.70, "host_service_fee": 24.45},
        "host_payout": 586.55,
    }


def test_infers_next_year_when_the_stay_crosses_new_year():
    body = make_body(dates="seg., 28 de dez.   sáb., 2 de jan.")

    booking = parse_confirmation(body, subject=SUBJECT, email_date=datetime(2026, 12, 20))

    assert (booking.check_in, booking.check_out) == (date(2026, 12, 28), date(2027, 1, 2))


def test_explicit_year_wins_over_inference():
    body = make_body(dates="sáb., 26 de dez. de 2026   sex., 8 de jan. de 2027")

    booking = parse_confirmation(body, subject=SUBJECT, email_date=datetime(2025, 1, 1))

    assert (booking.check_in, booking.check_out) == (date(2026, 12, 26), date(2027, 1, 8))


def test_usd_amounts_with_nbsp_between_symbol_and_value():
    lines = "Preço da acomodação para 2 noites   $\xa0241,30\n\nTaxa de serviço do anfitrião   -$\xa07,40"

    booking = parse_confirmation(
        make_body(payout_lines=lines, you_receive="$\xa0233,90"),
        subject=SUBJECT,
        email_date=EMAIL_DATE,
    )

    assert booking.currency == "USD"
    assert booking.payout_lines == {"accommodation": 241.30, "host_service_fee": 7.40}


@pytest.mark.parametrize(
    ("adjustment", "you_receive", "expected_field"),
    [
        ("R$21,00", "R$538,30", "other_income"),
        ("-R$21,00", "R$496,30", "discount"),
    ],
)
def test_nightly_price_adjustment_sign_picks_the_field(adjustment, you_receive, expected_field):
    lines = (
        f"Preço da acomodação para 2 noites   R$517,30\n\nAjuste de preço por noite   {adjustment}"
    )

    booking = parse_confirmation(
        make_body(payout_lines=lines, you_receive=you_receive),
        subject=SUBJECT,
        email_date=EMAIL_DATE,
    )

    assert booking.payout_lines[expected_field] == 21.00


def test_unknown_payout_label_is_an_error():
    lines = "Preço da acomodação para 2 noites   R$517,30\n\nTaxa misteriosa   R$10,00"

    with pytest.raises(ParseError, match="Unknown label"):
        parse_confirmation(
            make_body(payout_lines=lines, you_receive="R$527,30"),
            subject=SUBJECT,
            email_date=EMAIL_DATE,
        )


def test_lines_that_do_not_add_up_are_an_error():
    with pytest.raises(ParseError, match="add up"):
        parse_confirmation(
            make_body(you_receive="R$600,00"), subject=SUBJECT, email_date=EMAIL_DATE
        )


def test_guest_name_comes_from_the_subject():
    with pytest.raises(ParseError, match="guest_name"):
        parse_confirmation(make_body(), subject=None, email_date=EMAIL_DATE)
