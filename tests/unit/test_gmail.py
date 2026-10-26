"""The Gmail source, against a fake Gmail API (tests/conftest.py). No network."""

from datetime import UTC, datetime

import httpx
import pytest

from airbnb_booking_pipeline.sources import (
    GmailCredentials,
    GmailError,
    GmailSource,
    from_rfc822,
)
from airbnb_booking_pipeline.sources.gmail import DEFAULT_QUERY
from airbnb_booking_pipeline.synthetic import render_cancellation, render_confirmation, to_eml
from airbnb_booking_pipeline.synthetic.scenarios import CANCELLATIONS, CONFIRMATIONS

CONFIRMATION = to_eml(render_confirmation(CONFIRMATIONS[0].spec), "<booking@example.invalid>")
CANCELLATION = to_eml(render_cancellation(CANCELLATIONS[0].spec), "<cancel@example.invalid>")


def mailbox(n: int) -> dict[str, bytes]:
    return {f"id{i}": to_eml(render_confirmation(CONFIRMATIONS[0].spec), f"<{i}@x.invalid>") for i in range(n)}  # fmt: skip


def test_reads_each_email_as_its_eml_file_would_be_read(fake_gmail):
    emails = fake_gmail({"18f0a1": CONFIRMATION, "18f0a2": CANCELLATION}).source().fetch()

    assert emails == [
        from_rfc822(CONFIRMATION, default_id="a.eml"),
        from_rfc822(CANCELLATION, default_id="b.eml"),
    ]


@pytest.mark.parametrize("extra", [b"", b"x", b"xy"], ids=["len%3=0", "len%3=1", "len%3=2"])
def test_decodes_raw_emails_without_their_base64_padding(fake_gmail, extra):
    raw = CONFIRMATION + extra
    raw = raw[: len(raw) - len(raw) % 3] + extra  # every padding length, from the same email

    (email,) = fake_gmail({"18f0a1": raw}).source().fetch()

    assert email == from_rfc822(raw, default_id="a.eml")


def test_follows_every_page_of_the_list(fake_gmail):
    gmail = fake_gmail(mailbox(5), page_size=2)

    emails = gmail.source().fetch()

    assert [e.message_id for e in emails] == [f"<{i}@x.invalid>" for i in range(5)]
    lists = [r for r in gmail.requests if r.url.path.endswith("/messages")]
    assert [r.url.params.get("pageToken") for r in lists] == [None, "2", "4"]


def test_asks_for_a_token_then_lists_with_the_query(fake_gmail):
    gmail = fake_gmail({"18f0a1": CONFIRMATION})

    gmail.source(query="from:automated@airbnb.com newer_than:7d").fetch()

    token, listing, download = gmail.requests
    assert token.method == "POST"
    assert b"grant_type=refresh_token" in token.content
    assert b"refresh_token=test-refresh-token" in token.content
    assert listing.url.params["q"] == "from:automated@airbnb.com newer_than:7d"
    assert download.url.params["format"] == "raw"


def test_an_empty_mailbox_gives_no_emails(fake_gmail):
    assert fake_gmail({}).source().fetch() == []


def test_more_matches_than_the_limit_fail_instead_of_dropping_some(fake_gmail):
    gmail = fake_gmail(mailbox(5), page_size=2)

    with pytest.raises(GmailError, match="More than 3 emails match"):
        gmail.source(limit=3).fetch()

    # It stops listing and downloads nothing
    assert not any(r.url.params.get("format") for r in gmail.requests)


def test_exactly_the_limit_is_fine(fake_gmail):
    assert len(fake_gmail(mailbox(3), page_size=2).source(limit=3).fetch()) == 3


def test_a_refused_refresh_token_names_the_reason_and_hides_the_secrets(fake_gmail):
    with pytest.raises(GmailError) as error:
        fake_gmail({"18f0a1": CONFIRMATION}, refuse_token=True).source().fetch()

    assert "invalid_grant" in str(error.value)
    assert "test-refresh-token" not in str(error.value)
    assert "test-client-secret" not in str(error.value)


def _raise_connect_error(request):
    raise httpx.ConnectError("connection refused", request=request)


FAILURES = [
    pytest.param(_raise_connect_error, id="network"),
    pytest.param(lambda request: httpx.Response(503), id="http-error"),
    pytest.param(lambda request: httpx.Response(200, text="<html>"), id="not-json"),
    pytest.param(lambda request: httpx.Response(200, json={"messages": [{}]}), id="no-id"),
    # The list and the download get the same answer, which has no "raw"
    pytest.param(
        lambda request: httpx.Response(200, json={"messages": [{"id": "1"}]}), id="no-raw"
    ),
]


@pytest.mark.parametrize("failure", FAILURES)
def test_any_failure_to_read_gmail_is_a_gmail_error(failure):
    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "token"})
        return failure(request)

    source = GmailSource(
        GmailCredentials("id", "secret", "refresh"),
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(GmailError, match="Gmail could not be read"):
        source.fetch()


def test_a_token_response_without_a_token_is_a_gmail_error():
    source = GmailSource(
        GmailCredentials("id", "secret", "refresh"),
        http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))),
    )

    with pytest.raises(GmailError, match="KeyError"):
        source.fetch()


def test_without_message_id_or_date_the_gmail_id_and_arrival_stand_in(fake_gmail):
    raw = b"Subject: Hi\r\n\r\nbody\r\n"

    (email,) = fake_gmail({"18f0a1": raw}).source().fetch()

    assert email.message_id == "gmail:18f0a1"
    assert email.received_at == datetime(2026, 10, 20, 12, 30, tzinfo=UTC)


def test_from_env_reads_the_credentials_and_the_query(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "refresh")
    monkeypatch.setenv("GMAIL_QUERY", "label:airbnb")

    source = GmailSource.from_env()

    assert source.credentials == GmailCredentials("cid", "secret", "refresh")
    assert source.query == "label:airbnb"


def test_from_env_defaults_to_airbnb_emails_from_the_last_two_days(monkeypatch):
    for name in ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN"):
        monkeypatch.setenv(name, "x")
    monkeypatch.delenv("GMAIL_QUERY", raising=False)

    assert GmailSource.from_env().query == DEFAULT_QUERY
    assert "newer_than:2d" in DEFAULT_QUERY


def test_from_env_names_every_missing_variable(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "cid")
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("GOOGLE_REFRESH_TOKEN", "")

    with pytest.raises(ValueError, match="GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN"):
        GmailSource.from_env()


def test_the_secrets_stay_out_of_repr():
    text = repr(GmailCredentials("cid", "the-secret", "the-refresh-token"))

    assert "cid" in text
    assert "the-secret" not in text
    assert "the-refresh-token" not in text
