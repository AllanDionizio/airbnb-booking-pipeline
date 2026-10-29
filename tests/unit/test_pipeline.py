"""Routing, USD conversion and result codes of the pipeline, with a fake database and fake rates."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from airbnb_booking_pipeline.enrich import FxError, FxRate
from airbnb_booking_pipeline.load import CancellationResult, LoadResult
from airbnb_booking_pipeline.pipeline import Kind, Pipeline, business_tz_from_env, classify
from airbnb_booking_pipeline.sources import Email
from airbnb_booking_pipeline.synthetic import render_cancellation, render_confirmation
from airbnb_booking_pipeline.synthetic.scenarios import CANCELLATIONS, CONFIRMATIONS

FX = FxRate(rate=5.1234, quoted_on=date(2026, 6, 30), source="ptax")
SAO_PAULO = ZoneInfo("America/Sao_Paulo")


class FakeStore:
    def __init__(self, booking_code="CREATED", cancellation_code="FLAGGED"):
        self.booking_code = booking_code
        self.cancellation_code = cancellation_code
        self.calls = []

    def upsert_booking(self, booking, *, message_id, fx=None):
        self.calls.append(("booking", booking, message_id, fx))
        return LoadResult(self.booking_code, "from the fake store")

    def register_cancellation(self, notice, *, message_id, fx=None):
        self.calls.append(("cancellation", notice, message_id, fx))
        return CancellationResult(self.cancellation_code, "from the fake store")


class FakeRates:
    def __init__(self, error=None):
        self.error = error
        self.days = []

    def usd_brl(self, day):
        self.days.append(day)
        if self.error:
            raise self.error
        return FX


def email(name, message_id="msg-1", **changes) -> Email:
    """The synthetic email of a scenario, as a source would deliver it."""
    if any(sc.name == name for sc in CONFIRMATIONS):
        rendered = render_confirmation(next(sc.spec for sc in CONFIRMATIONS if sc.name == name))
    else:
        rendered = render_cancellation(next(sc.spec for sc in CANCELLATIONS if sc.name == name))
    built = Email(message_id, rendered.subject, rendered.body, rendered.email_date)
    return replace(built, **changes)


def run_one(mail, store=None, rates=None, tz=SAO_PAULO):
    store = store or FakeStore()
    rates = rates or FakeRates()
    return Pipeline(store, rates, tz).process(mail), store, rates


# --- Routing ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("simple", Kind.BOOKING),
        ("penalty_compact", Kind.CANCELLATION),
        ("full_refund_no_subject", Kind.CANCELLATION),  # routed by the body heading
        ("forwarded", Kind.BOOKING),
        ("forwarded_penalty", Kind.CANCELLATION),
    ],
)
def test_classifies_by_subject_then_by_body(name, kind):
    assert classify(email(name)) is kind


@pytest.mark.parametrize("prefix", ["Fwd: ", "FW: ", "Enc: ", "fwd:Fwd: "])
def test_forward_prefixes_do_not_hide_the_subject(prefix):
    for name in ("simple", "penalty_compact"):
        mail = email(name)
        assert classify(replace(mail, subject=prefix + mail.subject)) is classify(mail)


@pytest.mark.parametrize("name", ["penalty_compact", "forwarded_penalty"])
def test_a_cancellation_under_another_subject_is_routed_by_its_heading(name):
    assert classify(email(name, subject="Fwd: aviso")) is Kind.CANCELLATION


def test_the_heading_counts_only_on_a_line_of_its_own():
    mail = email("simple", subject=None, body="Reserva cancelada ontem, depois remarcada.")

    assert classify(mail) is Kind.UNRECOGNIZED


def test_a_forwarded_confirmation_reaches_the_database():
    outcome, store, _ = run_one(email("forwarded"))

    [(_, booking, _, _)] = store.calls
    assert (outcome.code, booking.confirmation_code) == ("CREATED", "HMTEST1014")


def test_a_confirmation_goes_to_upsert_booking():
    outcome, store, rates = run_one(email("simple", message_id="msg-7"))

    [(kind, booking, message_id, fx)] = store.calls
    assert (kind, booking.confirmation_code, message_id, fx) == (
        "booking",
        "HMTEST1001",
        "msg-7",
        None,
    )
    assert rates.days == []  # BRL: no rate asked for
    assert outcome.kind is Kind.BOOKING
    assert (outcome.code, outcome.needs_attention) == ("CREATED", False)
    assert outcome.confirmation_code == "HMTEST1001"


def test_a_cancellation_goes_to_register_cancellation():
    outcome, store, rates = run_one(email("full_refund"))

    [(kind, notice, _, fx)] = store.calls
    assert (kind, notice.confirmation_code, fx) == ("cancellation", "HMTEST2004", None)
    assert rates.days == []
    assert outcome.kind is Kind.CANCELLATION


def test_an_unrecognized_email_is_reported_and_not_loaded():
    outcome, store, _ = run_one(email("simple", subject="Lembrete: avalie seu hóspede"))

    assert store.calls == []
    assert (outcome.kind, outcome.code, outcome.needs_attention) == (
        Kind.UNRECOGNIZED,
        "UNRECOGNIZED",
        True,
    )


def test_a_confirmation_without_a_subject_is_unrecognized():
    # The guest name only comes from the subject: there is nothing to load
    outcome, store, _ = run_one(email("simple", subject=None))

    assert (store.calls, outcome.code) == ([], "UNRECOGNIZED")


def test_a_parse_error_is_reported_and_not_loaded():
    mail = email("simple")
    outcome, store, _ = run_one(replace(mail, body=mail.body.replace("VOCÊ RECEBE", "TOTAL")))

    assert store.calls == []
    assert (outcome.kind, outcome.code, outcome.needs_attention) == (
        Kind.BOOKING,
        "PARSE_ERROR",
        True,
    )


# --- USD -------------------------------------------------------------------------


def test_a_usd_booking_is_loaded_in_brl_with_its_rate():
    _, store, _ = run_one(email("usd"))

    [(_, booking, _, fx)] = store.calls
    assert fx == FX
    assert booking.currency == "BRL"
    assert booking.host_payout == round(289.20 * FX.rate, 2)


@pytest.mark.parametrize(
    ("tz", "day"),
    [
        (SAO_PAULO, date(2026, 7, 1)),  # 22:30 on the 1st in São Paulo
        (ZoneInfo("UTC"), date(2026, 7, 2)),
    ],
)
def test_the_rate_is_for_the_email_day_in_the_business_time_zone(tz, day):
    late_evening = datetime(2026, 7, 2, 1, 30, tzinfo=UTC)

    _, _, rates = run_one(email("usd", received_at=late_evening), tz=tz)

    assert rates.days == [day]


def test_a_usd_penalty_carries_its_rate():
    _, store, rates = run_one(email("penalty_usd"))

    [(_, notice, _, fx)] = store.calls
    assert (notice.currency, fx) == ("USD", FX)
    assert rates.days == [date(2026, 4, 10)]


@pytest.mark.parametrize("name", ["usd", "penalty_usd"])
def test_no_rate_means_fx_error_and_nothing_loaded(name):
    outcome, store, _ = run_one(email(name), rates=FakeRates(error=FxError("PTAX down")))

    assert store.calls == []
    assert (outcome.code, outcome.needs_attention) == ("FX_ERROR", True)
    assert outcome.confirmation_code is not None


# --- Result codes ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("code", "attention"),
    [
        ("CREATED", False),
        ("UPDATED", False),
        ("ALREADY_PROCESSED", False),
        ("ALREADY_EXISTS", False),
        ("QUEUED_UNKNOWN_LISTING", True),
        ("QUEUED_NO_CONTRACT", True),
        ("QUEUED_OVERLAP", True),
        ("INVALID_PAYLOAD", True),
    ],
)
def test_which_booking_results_need_attention(code, attention):
    outcome, _, _ = run_one(email("simple"), store=FakeStore(booking_code=code))

    assert (outcome.code, outcome.needs_attention) == (code, attention)


@pytest.mark.parametrize(
    ("code", "attention"),
    [
        ("FLAGGED", True),
        ("FLAGGED_REVIEW_ITEM", True),
        ("NOT_FOUND", True),
        ("ALREADY_CANCELLED", False),
        ("ALREADY_PROCESSED", False),
        ("INVALID_PAYLOAD", True),
    ],
)
def test_which_cancellation_results_need_attention(code, attention):
    outcome, _, _ = run_one(email("full_refund"), store=FakeStore(cancellation_code=code))

    assert (outcome.code, outcome.needs_attention) == (code, attention)


@pytest.mark.parametrize(
    ("code", "attention"), [("ALREADY_CANCELLED", True), ("ALREADY_PROCESSED", False)]
)
def test_an_undetermined_cancellation_needs_attention_the_first_time(code, attention):
    mail = email("full_refund")
    unreadable = replace(mail, body=mail.body.replace("Conforme", "Segundo"))

    outcome, store, _ = run_one(unreadable, store=FakeStore(cancellation_code=code))

    assert store.calls[0][1].outcome == "undetermined"
    assert outcome.needs_attention is attention


# --- A whole run -----------------------------------------------------------------


def test_emails_are_processed_oldest_first():
    newer = email("simple", message_id="newer", received_at=datetime(2026, 5, 21, tzinfo=UTC))
    older = email("simple", message_id="older", received_at=datetime(2026, 5, 20, tzinfo=UTC))
    store = FakeStore()

    Pipeline(store, FakeRates(), SAO_PAULO).run([newer, older])

    assert [call[2] for call in store.calls] == ["older", "newer"]


def test_the_report_counts_codes_and_lists_what_needs_attention():
    emails = [
        email("simple", message_id="a"),
        email("long_stay", message_id="b"),
        email("simple", message_id="c", subject="Lembrete"),
    ]

    report = Pipeline(FakeStore(), FakeRates(), SAO_PAULO).run(emails)

    assert report.counts() == {
        (Kind.BOOKING, "CREATED"): 2,
        (Kind.UNRECOGNIZED, "UNRECOGNIZED"): 1,
    }
    assert [o.message_id for o in report.attention] == ["c"]


def test_the_report_as_json_has_the_totals_and_the_attention_list():
    emails = [email("simple", message_id="a"), email("simple", message_id="c", subject="Oi")]

    data = Pipeline(FakeStore(), FakeRates(), SAO_PAULO).run(emails).to_dict()

    assert json.loads(json.dumps(data)) == {
        "processed": 2,
        "counts": [
            {"kind": "booking", "code": "CREATED", "emails": 1},
            {"kind": "unrecognized", "code": "UNRECOGNIZED", "emails": 1},
        ],
        "attention": [
            {
                "message_id": "c",
                "kind": "unrecognized",
                "code": "UNRECOGNIZED",
                "message": "Not a booking confirmation or a cancellation: 'Oi'",
                "needs_attention": True,
                "confirmation_code": None,
            }
        ],
    }


def test_a_database_error_stops_the_run():
    class BrokenStore(FakeStore):
        def upsert_booking(self, booking, *, message_id, fx=None):
            raise ConnectionError("database is down")

    with pytest.raises(ConnectionError):
        Pipeline(BrokenStore(), FakeRates(), SAO_PAULO).run([email("simple")])


@pytest.mark.parametrize("name", ["America/Sao Paulo", "../etc/passwd"])
def test_a_business_tz_that_is_not_a_time_zone_is_an_error(monkeypatch, name):
    monkeypatch.setenv("BUSINESS_TZ", name)

    with pytest.raises(ValueError, match="BUSINESS_TZ"):
        business_tz_from_env()


def test_the_business_tz_error_shows_a_stray_space(monkeypatch):
    monkeypatch.setenv("BUSINESS_TZ", "America/Sao_Paulo ")

    with pytest.raises(ValueError, match="'America/Sao_Paulo '"):
        business_tz_from_env()
