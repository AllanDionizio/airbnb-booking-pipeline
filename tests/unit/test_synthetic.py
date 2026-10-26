"""Cases for the synthetic email generator."""

import re

import pytest

from airbnb_booking_pipeline.synthetic import LISTINGS, format_money, render_cancellation
from airbnb_booking_pipeline.synthetic.fixtures import write_fixtures
from airbnb_booking_pipeline.synthetic.scenarios import CANCELLATIONS, CONFIRMATIONS

SPECS = [sc.spec for sc in (*CONFIRMATIONS, *CANCELLATIONS)]


@pytest.mark.parametrize(
    ("value", "currency", "sep", "expected"),
    [
        (517.3, "BRL", "", "R$517,30"),
        (1904.0, "BRL", " ", "R$ 1.904,00"),
        (-1044.0, "BRL", "\xa0", "-R$\xa01.044,00"),
        (-7.4, "USD", "\xa0", "-$\xa07,40"),
        (1234567.891, "BRL", "", "R$1.234.567,89"),
    ],
)
def test_format_money_uses_the_brazilian_number_format(value, currency, sep, expected):
    assert format_money(value, currency, sep) == expected


def test_there_are_20_scenarios_with_unique_names():
    names = [sc.name for sc in (*CONFIRMATIONS, *CANCELLATIONS)]

    assert len(names) == len(set(names)) == 20


def test_scenarios_use_only_fictional_identifiers():
    # Guardrail for the publication: nothing that could come from a real inbox
    codes = [spec.code for spec in SPECS]

    assert all(re.fullmatch(r"HMTEST\d{4}", code) for code in codes)
    assert len(codes) == len(set(codes))
    assert all(spec.listing in LISTINGS for spec in SPECS)
    assert all(re.fullmatch(r"Guest [A-Z][a-z]+", spec.guest) for spec in SPECS)


@pytest.mark.parametrize("scenario", CANCELLATIONS, ids=lambda sc: sc.name)
def test_penalty_emails_carry_the_full_refund_button(scenario):
    body = render_cancellation(scenario.spec).body

    # The trap the cancellation parser has to survive
    assert ("Emitir reembolso integral" in body) == (scenario.spec.penalty is not None)


def test_write_fixtures_removes_fixtures_of_renamed_scenarios(tmp_path):
    stale = tmp_path / "confirmations" / "old_name.json"
    stale.parent.mkdir()
    stale.write_text("{}", encoding="utf-8")

    written = write_fixtures(tmp_path)

    assert not stale.exists()
    assert sorted(written) == sorted(tmp_path.rglob("*.json"))
    assert len(written) == 20
