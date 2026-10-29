"""
Command line: `airbnb-pipeline run --inbox DIR` or `airbnb-pipeline run --gmail`
(or `python -m airbnb_booking_pipeline`).

Reads every .eml file in the inbox, or the emails that match the Gmail
query, runs the pipeline against the database in DATABASE_URL and prints a
summary plus the emails that need a person. The exit code is 0 whenever the
run finishes, attention items included: they are part of the normal flow,
not a failure. A database error, or Gmail failing to answer, stops the run
with exit code 1; what was loaded stays, and rerunning is safe.
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import TextIO

import psycopg

from . import runner
from .pipeline import RunReport
from .sources import GmailError, GmailSource, LocalSource


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="airbnb-pipeline", description="Load Airbnb host emails into Postgres."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="process the emails from a directory or from Gmail")
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument("--inbox", type=Path, help="directory with the .eml files")
    source.add_argument(
        "--gmail",
        action="store_true",
        help="the mailbox of $GOOGLE_REFRESH_TOKEN, searched with $GMAIL_QUERY",
    )
    run.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL"),
        help="Postgres connection string (default: $DATABASE_URL)",
    )
    run.add_argument("-v", "--verbose", action="store_true", help="log every email")
    return parser


def print_report(report: RunReport, out: TextIO) -> None:
    print(f"{len(report.outcomes)} emails processed", file=out)
    for (kind, code), n in sorted(report.counts().items()):
        print(f"  {kind:<13} {code:<24} {n:>4}", file=out)

    attention = report.attention
    if attention:
        print(f"\nNeeds attention ({len(attention)}):", file=out)
        for o in attention:
            print(
                f"  {o.code:<24} {o.kind:<13} {o.confirmation_code or '-':<12} "
                f"{o.message_id}  {o.message}",
                file=out,
            )


def main(argv: list[str] | None = None, out: TextIO = sys.stdout) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if not args.database_url:
        parser.error("set DATABASE_URL or pass --database-url")
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        runner.check_settings()
        source = GmailSource.from_env() if args.gmail else LocalSource(args.inbox)
        emails = source.fetch()
    except (FileNotFoundError, ValueError) as exc:  # a missing inbox or setting
        parser.error(str(exc))
    except GmailError as exc:
        print(f"airbnb-pipeline: {exc}", file=sys.stderr)
        return 1

    try:
        report = runner.run_emails(emails, args.database_url)
    except psycopg.Error as exc:
        print(f"airbnb-pipeline: database error: {exc}", file=sys.stderr)
        return 1

    print_report(report, out)
    return 0
