"""Hand-written cases for the cancellation parser."""

import pytest

from airbnb_booking_pipeline.parsers import CancellationOutcome, ParseError, parse_cancellation

# Airbnb writes date ranges with thin spaces around an en dash
DATE_RANGE = "4 de mai.\u2009\u2013\u200922 de jun."
SUBJECT = f"Cancelado: reserva HMTEST0002 de {DATE_RANGE}"

PENALTY_BODY = f"""Infelizmente, o seu hóspede, Guest, precisou cancelar a
reserva HMTEST0002 de {DATE_RANGE}. Seu
calendário foi atualizado.

Conforme sua política de cancelamento, seu recebimento foi
atualizado para {{amount}}.

Você também pode emitir o reembolso no valor total para o
usuário clicando no botão abaixo.

Emitir reembolso integral
"""

FULL_REFUND_BODY = f"""Infelizmente, o seu hóspede, Guest, precisou cancelar a
reserva HMTEST0002 de {DATE_RANGE}. Seu
calendário foi atualizado.

Conforme sua política de cancelamento, o hóspede recebeu um
reembolso integral.
"""


@pytest.mark.parametrize(
    ("amount", "expected_amount", "expected_currency"),
    [
        pytest.param("R$123,45", 123.45, "BRL", id="no-space"),
        pytest.param("R$\xa0123,45", 123.45, "BRL", id="nbsp"),
        pytest.param("$\xa01.234,56\xa0USD", 1234.56, "USD", id="usd-suffix"),
    ],
)
def test_penalty_wins_over_the_full_refund_call_to_action(
    amount, expected_amount, expected_currency
):
    # The body also says "Emitir reembolso integral": a substring match on
    # "reembolso integral" would call this a full refund.
    notice = parse_cancellation(PENALTY_BODY.format(amount=amount), subject=SUBJECT)

    assert notice.outcome is CancellationOutcome.PENALTY
    assert notice.penalty_amount == expected_amount
    assert notice.currency == expected_currency


def test_full_refund():
    notice = parse_cancellation(FULL_REFUND_BODY, subject=SUBJECT)

    assert notice.to_dict() == {
        "confirmation_code": "HMTEST0002",
        "outcome": "full_refund",
        "penalty_amount": None,
        "currency": None,
    }


def test_code_falls_back_to_the_body_when_there_is_no_subject():
    notice = parse_cancellation(FULL_REFUND_BODY, subject=None)

    assert notice.confirmation_code == "HMTEST0002"


def test_unrecognized_money_sentence_is_undetermined_not_an_error():
    body = "precisou cancelar a reserva HMTEST0002 de 4 de mai. Texto novo do Airbnb."

    notice = parse_cancellation(body, subject=None)

    assert notice.outcome is CancellationOutcome.UNDETERMINED
    assert notice.penalty_amount is None
    assert notice.currency is None  # no amount, so no currency to report


def test_missing_confirmation_code_is_an_error():
    with pytest.raises(ParseError, match="confirmation code"):
        parse_cancellation("Sem código aqui.", subject="Cancelado")
