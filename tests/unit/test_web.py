"""The HTTP layer, with a fake source and a fake run: status codes, JSON, and settings at startup."""

from datetime import UTC, datetime

import psycopg
import pytest

from airbnb_booking_pipeline.pipeline import Kind, Outcome, RunReport
from airbnb_booking_pipeline.sources import Email, GmailError, GmailSource, LocalSource
from airbnb_booking_pipeline.web import create_app, run_from_env, source_from_env

EMAIL = Email("<a@example.invalid>", "Oi", "", datetime(2026, 10, 1, tzinfo=UTC))
REPORT = RunReport(
    [Outcome("<a@example.invalid>", Kind.UNRECOGNIZED, "UNRECOGNIZED", "not ours", True)]
)
SETTINGS = ["GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "INBOX_DIR"]


class FakeSource:
    def __init__(self, error=None):
        self.error = error

    def fetch(self):
        if self.error:
            raise self.error
        return [EMAIL]


class FakeRun:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def __call__(self, emails):
        self.calls.append(list(emails))
        if self.error:
            raise self.error
        return REPORT


def client(source=None, run=None):
    return create_app(source or FakeSource(), run or FakeRun()).test_client()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in [*SETTINGS, "DATABASE_URL"]:
        monkeypatch.delenv(name, raising=False)


# --- Endpoints -------------------------------------------------------------------


def test_the_root_says_the_service_is_up_and_which_source_it_reads():
    response = client().get("/")

    assert (response.status_code, response.json) == (200, {"status": "ok", "source": "FakeSource"})


def test_sync_runs_the_emails_of_the_source_and_returns_the_report():
    run = FakeRun()

    response = client(run=run).post("/sync")

    assert run.calls == [[EMAIL]]
    assert response.status_code == 200
    assert response.json == REPORT.to_dict()


def test_sync_only_answers_post():
    assert client().get("/sync").status_code == 405


def test_gmail_failing_is_a_502_and_runs_nothing():
    run = FakeRun()

    response = client(FakeSource(GmailError("HTTP 503 from the Gmail API")), run).post("/sync")

    assert response.status_code == 502
    assert response.json == {"error": "Gmail: HTTP 503 from the Gmail API"}
    assert run.calls == []


def test_the_database_failing_is_a_503_without_its_details():
    error = psycopg.OperationalError("connection to server at db.internal failed")

    response = client(run=FakeRun(error)).post("/sync")

    assert response.status_code == 503
    assert response.json == {"error": "database error"}


# --- Settings at startup ---------------------------------------------------------


def test_reads_the_inbox_directory_without_gmail_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("INBOX_DIR", str(tmp_path))

    source = source_from_env()

    assert isinstance(source, LocalSource)
    assert source.inbox == tmp_path


def test_gmail_credentials_win_over_the_inbox_directory(monkeypatch, tmp_path):
    monkeypatch.setenv("INBOX_DIR", str(tmp_path))
    for name in SETTINGS[:3]:
        monkeypatch.setenv(name, "x")

    assert isinstance(source_from_env(), GmailSource)


def test_half_the_gmail_credentials_is_an_error_not_a_fallback(monkeypatch, tmp_path):
    monkeypatch.setenv("INBOX_DIR", str(tmp_path))
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")

    with pytest.raises(ValueError, match="GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN"):
        source_from_env()


def test_no_source_is_an_error():
    with pytest.raises(ValueError, match="INBOX_DIR"):
        source_from_env()


def test_an_inbox_that_does_not_exist_is_an_error(monkeypatch, tmp_path):
    monkeypatch.setenv("INBOX_DIR", str(tmp_path / "missing"))

    with pytest.raises(ValueError, match="not a directory"):
        source_from_env()


def test_no_database_url_is_an_error():
    with pytest.raises(ValueError, match="DATABASE_URL"):
        run_from_env()


def test_the_app_fails_at_startup_without_settings():
    with pytest.raises(ValueError):
        create_app()
