# 4. No orchestrator

**Status:** Accepted

## Context

The job has one step: read the new emails, parse them, convert USD amounts and load the results. There is one source, no dependency between tasks and no backfill over date ranges, and a run is short. It has to run on a schedule, unattended, and recover from a missed or failed run without anyone stepping in.

## Decision

A scheduler calls the pipeline over HTTP, with no orchestrator in between.

- In production, Cloud Scheduler called a private Cloud Run service with an OIDC token, and each call ran the pipeline once. [`web.py`](../../src/airbnb_booking_pipeline/web.py) keeps that shape here: `POST /sync` runs the pipeline once and answers with the run report.
- Recovery is a rerun, and a rerun is safe because every email is idempotent: the `processed_messages` ledger for confirmations, the unique message id on `cancellation_events` for cancellations. The default Gmail search here covers the last two days, so as long as runs are less than two days apart, a missed run is picked up by the next one.
- The HTTP status tells the caller what happened. 200 means the run finished, attention items included. 502 means Gmail failed and 503 means the database failed; both are worth retrying.
- Locally, the same run is the command line (`airbnb-pipeline run`) or `make demo`. In CI, the integration tests run it end to end against Postgres.

## Alternatives considered

- **Airflow, managed (Cloud Composer) or self-hosted.** A scheduler, a metadata database and workers, all running and paid for around the clock, to run one task. The things Airflow is good at (dependencies between tasks, retries per task, backfills, a UI over many DAGs) are things this job does not have.
- **A lighter orchestrator (Dagster, Prefect).** Less to run, but still a second system to deploy, secure and watch, for a job whose whole state lives in the database it writes to.

## Consequences

- There are only a few moving parts: a scheduler entry, a container, a database and a secret store. The deployment can be explained in one paragraph (see [How it ran in production](../../README.md#how-it-ran-in-production)).
- There is no run history outside the logs and the database. The run report of a past run exists only in the logs; the ledger and the review queue record what happened to each email.
- The decision holds while the job stays one step. An orchestrator earns its place when steps need their own schedules, retries or backfills: for example, a transformation layer that must run only after a successful load and be rebuilt over past months, or more sources on different timetables. The planned dbt layer is the first point to look at this again.
