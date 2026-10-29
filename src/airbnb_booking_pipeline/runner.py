"""One run against the real database and exchange rates, shared by the CLI and the web app."""

from collections.abc import Iterable

import psycopg

from .enrich import PtaxClient, fallback_rate_from_env
from .load import PostgresStore
from .pipeline import Pipeline, RunReport, business_tz_from_env
from .sources import Email


def check_settings() -> None:
    """
    Read the optional settings a run uses (BUSINESS_TZ, USD_BRL_FALLBACK), so
    one that does not parse raises ValueError before any email is read.
    """
    business_tz_from_env()
    fallback_rate_from_env()


def run_emails(emails: Iterable[Email], database_url: str) -> RunReport:
    """
    Run the pipeline over `emails` against the database at `database_url`,
    with PTAX rates. Raises psycopg.Error when the database fails; what was
    loaded stays.
    """
    with psycopg.connect(database_url, autocommit=True) as conn:
        pipeline = Pipeline(PostgresStore(conn), PtaxClient.from_env(), business_tz_from_env())
        return pipeline.run(emails)
