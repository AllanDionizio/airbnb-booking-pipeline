"""Cases for the PTAX client and the USD → BRL conversion. No network: httpx.MockTransport."""

from dataclasses import replace
from datetime import date

import httpx
import pytest

from airbnb_booking_pipeline.enrich import FxError, FxRate, PtaxClient, convert_booking
from airbnb_booking_pipeline.parsers import parse_confirmation
from airbnb_booking_pipeline.synthetic import render_confirmation
from airbnb_booking_pipeline.synthetic.scenarios import CONFIRMATIONS

# Monday 12/10/2026 is a holiday: the latest quote on or before it is Friday's
HOLIDAY = date(2026, 10, 12)
QUOTES = [
    {"cotacaoCompra": 5.1228, "cotacaoVenda": 5.1234, "dataHoraCotacao": "2026-10-09 13:05:11.204518"},
    {"cotacaoCompra": 5.0871, "cotacaoVenda": 5.0877, "dataHoraCotacao": "2026-10-08 13:04:52.617003"},
]  # fmt: skip


def client_for(handler, fallback_rate=None) -> PtaxClient:
    return PtaxClient(httpx.Client(transport=httpx.MockTransport(handler)), fallback_rate)


def answer(quotes):
    return lambda request: httpx.Response(200, json={"value": quotes})


def test_takes_the_latest_quote_on_or_before_the_day():
    fx = client_for(answer(QUOTES)).usd_brl(HOLIDAY)

    assert fx == FxRate(rate=5.1234, quoted_on=date(2026, 10, 9), source="ptax")


def test_asks_for_a_ten_day_window_in_odata_format():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"value": QUOTES})

    client_for(handler).usd_brl(HOLIDAY)

    params = requests[0].url.params
    assert params["@dataInicial"] == "'10-02-2026'"
    assert params["@dataFinalCotacao"] == "'10-12-2026'"
    assert params["$format"] == "json"


def test_caches_the_rate_per_day():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"value": QUOTES})

    client = client_for(handler)
    client.usd_brl(HOLIDAY)
    client.usd_brl(HOLIDAY)
    client.usd_brl(date(2026, 10, 11))

    assert len(calls) == 2


def _raise_connect_error(request):
    raise httpx.ConnectError("connection refused", request=request)


FAILURES = [
    pytest.param(lambda request: httpx.Response(503), id="http-error"),
    pytest.param(answer([]), id="empty-window"),
    pytest.param(
        lambda request: httpx.Response(200, text="<html>maintenance</html>"), id="not-json"
    ),
    pytest.param(lambda request: httpx.Response(200, json={"error": "x"}), id="unexpected-shape"),
    pytest.param(_raise_connect_error, id="network"),
]


@pytest.mark.parametrize("handler", FAILURES)
def test_falls_back_when_ptax_fails(handler):
    fx = client_for(handler, fallback_rate=5.4).usd_brl(HOLIDAY)

    assert fx == FxRate(rate=5.4, quoted_on=HOLIDAY, source="fallback")


@pytest.mark.parametrize("handler", FAILURES)
def test_without_a_fallback_a_ptax_failure_is_an_error(handler):
    with pytest.raises(FxError, match="no fallback"):
        client_for(handler).usd_brl(HOLIDAY)


def test_the_fallback_is_not_cached():
    responses = iter([httpx.Response(503), httpx.Response(200, json={"value": QUOTES})])
    client = client_for(lambda request: next(responses), fallback_rate=5.4)

    assert client.usd_brl(HOLIDAY).source == "fallback"
    assert client.usd_brl(HOLIDAY).source == "ptax"


def test_fallback_rate_from_the_environment_accepts_a_decimal_comma(monkeypatch):
    monkeypatch.setenv("USD_BRL_FALLBACK", "5,43")
    transport = httpx.MockTransport(lambda request: httpx.Response(503))

    fx = PtaxClient.from_env(httpx.Client(transport=transport)).usd_brl(HOLIDAY)

    assert fx.rate == 5.43


def _usd_booking():
    scenario = next(sc for sc in CONFIRMATIONS if sc.name == "usd")
    email = render_confirmation(scenario.spec)
    return parse_confirmation(email.body, subject=email.subject, email_date=email.email_date)


def test_converts_every_amount_and_rounds_each_to_cents():
    booking = _usd_booking()
    fx = FxRate(rate=5.1234, quoted_on=date(2026, 7, 1), source="ptax")

    converted = convert_booking(booking, fx)

    assert converted.currency == "BRL"
    assert converted.payout_lines == {
        "accommodation": 1460.17,  # 285.00 x 5.1234 = 1460.169
        "cleaning_fee": 153.70,  # 30.00 x 5.1234 = 153.702
        "discount": 76.85,  # 15.00 x 5.1234 = 76.851
        "host_service_fee": 55.33,  # 10.80 x 5.1234 = 55.33272
    }
    assert converted.host_payout == 1481.69  # 289.20 x 5.1234 = 1481.68728
    assert booking.currency == "USD"  # the original is left untouched


def test_only_usd_bookings_are_converted():
    brl_booking = replace(_usd_booking(), currency="BRL")

    with pytest.raises(ValueError, match="Only USD"):
        convert_booking(brl_booking, FxRate(rate=5.0, quoted_on=date(2026, 7, 1), source="ptax"))


@pytest.mark.parametrize("raw", ["abc", "0", "-5,43", "inf", "5_43", "1,000.50"])
def test_a_fallback_rate_that_is_not_a_positive_number_is_an_error(monkeypatch, raw):
    monkeypatch.setenv("USD_BRL_FALLBACK", raw)

    with pytest.raises(ValueError, match="USD_BRL_FALLBACK"):
        PtaxClient.from_env()
