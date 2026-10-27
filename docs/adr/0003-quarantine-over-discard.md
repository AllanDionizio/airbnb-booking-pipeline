# 3. Quarantine instead of discard

**Status:** Accepted

## Context

A confirmation email can parse cleanly and still not be a booking the database can take:

- the listing in the email is not registered;
- no owner contract is in force on the check-in date;
- the dates clash with a live booking of the same listing.

Each case is a real booking with real money behind it. Dropping it with a log line loses it quietly. Writing it anyway puts wrong data into a back-office that people act on, and the exclusion constraints on `bookings` and `owner_contracts` exist to stop exactly that.

Some emails never get that far: they are not a confirmation or a cancellation, the parser rejects them, or a USD amount has no rate to be converted with.

## Decision

Nothing the pipeline cannot settle is dropped or guessed. It is kept, with the reason, where a person will see it.

- [`upsert_booking`](../../sql/002_upsert_booking.sql) sends those three cases to `pipeline.booking_review_queue`. Each item has a typed reason (`unknown_listing`, `no_contract`, `overlap`), a readable detail, the ids of any conflicting bookings and the whole parsed payload, so nothing has to be typed in again. An item moves from `open` to `resolved` or `discarded` instead of being deleted, so the queue keeps its own history.
- The email's outcome (`QUEUED_UNKNOWN_LISTING`, `QUEUED_NO_CONTRACT`, `QUEUED_OVERLAP`) goes into the `processed_messages` ledger like any other, so the same email does not queue twice.
- `INVALID_PAYLOAD` is the exception. A malformed payload is a bug in the loader, not something a person can fix in the queue, so nothing is written, not even to the ledger. The same email goes in once the bug is fixed.
- A cancellation for a booking that is still in the queue is matched to the open item (`FLAGGED_REVIEW_ITEM`) instead of coming back `NOT_FOUND`.
- In [`pipeline.py`](../../src/airbnb_booking_pipeline/pipeline.py), emails that never reach the database end in `UNRECOGNIZED`, `PARSE_ERROR` or `FX_ERROR`. The run goes on, and each one is listed in the run report as needing attention. The command exits with 0 and `POST /sync` answers 200, because a quarantined email is part of a normal run, not a failure. Only a failure of the database or of Gmail stops a run.

## Alternatives considered

- **Discard and log.** Nobody reads logs looking for a missing booking. The lesson came from production: a Gmail search that matched only confirmation subjects kept cancellations out for a while, with no error anywhere.
- **Fail the run.** One bad email would block every email after it until someone fixed it.
- **Write the booking and flag it.** That breaks the rules the constraints enforce (one live booking per listing and night, one contract per listing and day), and every query on `bookings` would have to remember to filter the flag out.

## Consequences

- Nothing disappears quietly, and the constraints on `bookings` stay strict.
- Someone has to work the queue. Each run reports the emails it queued, but the backlog of open items is a query on `booking_review_queue`: this repo has no screen or alert for it.
- Resolving an item needs a step this repo does not ship. In production, the back-office resolved an item in one transaction: it ran the rules again on the stored payload, created the booking as a draft for a person to confirm, and marked the item `resolved`. Here the queue stores everything that step needs (payload, reason, status). Replaying the original email does not do it: the ledger already holds the email's queued outcome, so it comes back `ALREADY_PROCESSED`.
