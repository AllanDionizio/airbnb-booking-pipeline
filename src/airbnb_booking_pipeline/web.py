"""
The pipeline over HTTP, for the container: `GET /` says the service is up,
`POST /sync` runs the pipeline once and answers with the RunReport as JSON.

The emails come from Gmail when any GOOGLE_* credential is set, and from
the .eml files in INBOX_DIR otherwise, so the container also runs the demo
without a Google account. Every setting is read when the app is created: a
missing DATABASE_URL, a missing INBOX_DIR or half the Gmail credentials stop
the container at startup instead of failing at the first sync.

The status code follows the CLI's exit code. A run that finishes is a 200,
attention items included. Gmail failing to answer is a 502 and the database
failing a 503; both are safe to retry, since every email is idempotent.

/sync has no authentication of its own: deploy it where the platform checks
the caller, as Cloud Run does without --allow-unauthenticated.

Run it with `gunicorn "airbnb_booking_pipeline.web:create_app()"`.
"""

import logging
import os
from collections.abc import Callable, Iterable
from pathlib import Path

import psycopg
from flask import Flask

from . import runner
from .pipeline import RunReport
from .sources import Email, GmailError, GmailSource, LocalSource, Source
from .sources.gmail import ENV_CREDENTIALS

log = logging.getLogger(__name__)

Run = Callable[[Iterable[Email]], RunReport]


def source_from_env() -> Source:
    """Gmail when any GOOGLE_* credential is set (then all three must be), else INBOX_DIR."""
    if any(os.environ.get(name) for name in ENV_CREDENTIALS):
        return GmailSource.from_env()
    inbox = os.environ.get("INBOX_DIR")
    if not inbox:
        raise ValueError(
            "set the GOOGLE_* credentials to read Gmail, or INBOX_DIR to read .eml files"
        )
    if not Path(inbox).is_dir():
        raise ValueError(f"INBOX_DIR is not a directory: {inbox}")
    return LocalSource(Path(inbox))


def run_from_env() -> Run:
    """The pipeline against the database in DATABASE_URL."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise ValueError("set DATABASE_URL")
    return lambda emails: runner.run_emails(emails, database_url)


def create_app(source: Source | None = None, run: Run | None = None) -> Flask:
    """The app; `source` and `run` default to the ones the environment describes."""
    source = source or source_from_env()
    run = run or run_from_env()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    app = Flask(__name__)

    @app.get("/")
    def status():
        return {"status": "ok", "source": type(source).__name__}

    @app.post("/sync")
    def sync():
        try:
            emails = source.fetch()
        except GmailError as exc:
            log.error("Gmail: %s", exc)
            return {"error": f"Gmail: {exc}"}, 502
        try:
            report = run(emails)
        except psycopg.Error:
            # The details (host, user) go to the log, not to the caller
            log.exception("database error")
            return {"error": "database error"}, 503
        return report.to_dict()

    return app
