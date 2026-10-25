"""Reading .eml files: the local source and the RFC 822 parsing every source shares."""

from datetime import UTC, datetime

import pytest

from airbnb_booking_pipeline.sources import LocalSource, from_rfc822
from airbnb_booking_pipeline.synthetic import render_cancellation, render_confirmation, to_eml
from airbnb_booking_pipeline.synthetic.scenarios import CANCELLATIONS, CONFIRMATIONS

RENDERED = [render_confirmation(sc.spec) for sc in CONFIRMATIONS] + [
    render_cancellation(sc.spec) for sc in CANCELLATIONS
]


@pytest.mark.parametrize("rendered", RENDERED)
def test_an_eml_round_trip_keeps_the_email_intact(rendered):
    email = from_rfc822(to_eml(rendered, "<a@example.invalid>"), default_id="a.eml")

    # nbsp, trailing spaces and line breaks included: the parsers depend on them
    assert email.body == rendered.body
    assert email.subject == rendered.subject
    assert email.received_at == rendered.email_date
    assert email.message_id == "<a@example.invalid>"


def test_falls_back_to_the_default_id_without_a_message_id():
    raw = b"Subject: Hi\r\nDate: Fri, 09 Oct 2026 10:00:00 -0300\r\n\r\nbody\r\n"

    assert from_rfc822(raw, default_id="hi.eml").message_id == "hi.eml"


def test_a_date_without_a_time_zone_is_utc():
    raw = b"Subject: Hi\r\nDate: Fri, 09 Oct 2026 10:00:00 -0000\r\n\r\nbody\r\n"

    email = from_rfc822(raw, default_id="hi.eml")

    assert email.received_at == datetime(2026, 10, 9, 10, tzinfo=UTC)


@pytest.mark.parametrize("date_header", [b"", b"Date: sometime last week\r\n"])
def test_rejects_an_email_without_a_valid_date(date_header):
    raw = b"Subject: Hi\r\n" + date_header + b"\r\nbody\r\n"

    with pytest.raises(ValueError, match=r"hi\.eml: the email has no valid Date header"):
        from_rfc822(raw, default_id="hi.eml")


def test_reads_only_the_eml_files_in_name_order(tmp_path):
    for name in ("b.eml", "a.eml"):
        (tmp_path / name).write_bytes(to_eml(RENDERED[0], f"<{name}@example.invalid>"))
    (tmp_path / "notes.txt").write_text("not an email")

    emails = LocalSource(tmp_path).fetch()

    assert [e.message_id for e in emails] == ["<a.eml@example.invalid>", "<b.eml@example.invalid>"]


def test_a_missing_inbox_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        LocalSource(tmp_path / "missing").fetch()
