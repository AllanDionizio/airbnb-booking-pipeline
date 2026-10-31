-- Tables for the booking pipeline.
--
-- Everything lives in the "pipeline" schema, so the integration tests can
-- drop and rebuild it without touching anything else in the database.
-- Amounts are numeric(12,2) in BRL: USD bookings arrive already converted,
-- with the rate that was used.

create extension if not exists btree_gist;  -- "=" inside the exclusion constraints

create schema if not exists pipeline;


-- Properties under management. An Airbnb email names a listing only by its
-- title, so the title (case and outer spaces ignored) is the lookup key.
create table pipeline.listings (
    listing_id  bigint generated always as identity primary key,
    platform    text not null default 'airbnb',
    title       text not null,
    created_at  timestamptz not null default now()
);

create unique index listings_platform_title_key
    on pipeline.listings (platform, lower(btrim(title)));


-- Management contracts between the company and each owner. A booking takes
-- the contract in force on its check-in date. valid_to is exclusive and
-- null while the contract is open-ended.
create table pipeline.owner_contracts (
    contract_id     bigint generated always as identity primary key,
    listing_id      bigint not null references pipeline.listings,
    commission_pct  numeric(5,2) not null check (commission_pct between 0 and 100),
    valid_from      date not null,
    valid_to        date,
    check (valid_to is null or valid_to > valid_from),
    -- At most one contract in force per listing on any given day
    exclude using gist (
        listing_id with =,
        daterange(valid_from, valid_to) with &&
    )
);


-- Bookings created from confirmation emails. They start as 'draft': a person
-- confirms them in the back office, and the pipeline never overwrites a
-- booking once it left draft.
create table pipeline.bookings (
    booking_id         bigint generated always as identity primary key,
    platform           text not null,
    confirmation_code  text not null,
    listing_id         bigint not null references pipeline.listings,
    contract_id        bigint not null references pipeline.owner_contracts,
    status             text not null default 'draft'
                       check (status in ('draft', 'confirmed', 'cancelled')),
    guest_name         text not null,
    check_in           date not null,
    check_out          date not null check (check_out > check_in),
    num_guests         integer check (num_guests > 0),

    accommodation      numeric(12,2) not null check (accommodation > 0),
    cleaning_fee       numeric(12,2) not null default 0,
    pet_fee            numeric(12,2) not null default 0,
    other_income       numeric(12,2) not null default 0,
    discount           numeric(12,2) not null default 0,  -- absolute value
    host_service_fee   numeric(12,2) not null default 0,  -- absolute value
    -- Derived, never sent: one source of truth for the payout
    host_payout        numeric(12,2) generated always as (
                           accommodation + cleaning_fee + pet_fee + other_income
                           - discount - host_service_fee
                       ) stored,

    original_currency  text not null check (original_currency in ('BRL', 'USD')),
    fx_rate            numeric(10,4),
    fx_quoted_on       date,
    fx_source          text check (fx_source in ('ptax', 'fallback')),

    last_message_id    text not null,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),

    unique (platform, confirmation_code),
    -- A USD booking carries the rate it was converted with; a BRL one none
    check (
        case original_currency
            when 'USD' then fx_rate is not null and fx_quoted_on is not null and fx_source is not null
            else fx_rate is null and fx_quoted_on is null and fx_source is null
        end
    ),
    -- Backstop for the overlap check in upsert_booking: two live bookings of
    -- the same listing can never share a night, even under concurrent loads
    exclude using gist (
        listing_id with =,
        daterange(check_in, check_out) with &&
    ) where (status <> 'cancelled')
);


-- Quarantine: emails that parsed fine but cannot become a booking without a
-- person. The original payload is kept so the booking can be loaded from it
-- once the cause is fixed (listing registered, contract added, conflict
-- solved). Replaying the email itself returns ALREADY_PROCESSED.
create table pipeline.booking_review_queue (
    review_id                bigint generated always as identity primary key,
    reason                   text not null
                             check (reason in ('unknown_listing', 'no_contract', 'overlap')),
    status                   text not null default 'open'
                             check (status in ('open', 'resolved', 'discarded')),
    detail                   text not null,
    platform                 text not null,
    confirmation_code        text not null,
    listing_title            text,
    check_in                 date not null,
    check_out                date not null,
    conflicting_booking_ids  bigint[],
    payload                  jsonb not null,
    source_message_id        text not null,
    created_at               timestamptz not null default now()
);


-- Idempotency ledger: one row per email the database has acted on, with the
-- outcome. Reprocessing a message returns the recorded outcome and changes
-- nothing, whatever happened to the booking or the queue item since.
create table pipeline.processed_messages (
    message_id    text primary key,
    result_code   text not null,
    booking_id    bigint references pipeline.bookings,
    review_id     bigint references pipeline.booking_review_queue,
    processed_at  timestamptz not null default now()
);


-- One row per cancellation email. The pipeline never cancels anything: it
-- records the event and points it at the booking (or the review-queue item)
-- it concerns, and a person confirms the cancellation. Rows never change
-- after insert, so message_id doubles as the idempotency key.
create table pipeline.cancellation_events (
    event_id           bigint generated always as identity primary key,
    message_id         text not null unique,
    platform           text not null,
    confirmation_code  text not null,
    outcome            text not null
                       check (outcome in ('full_refund', 'penalty', 'undetermined')),
    -- The penalty as the email states it, and in BRL. Both null without one.
    penalty_amount     numeric(12,2) check (penalty_amount > 0),
    currency           text check (currency in ('BRL', 'USD')),
    penalty_brl        numeric(12,2),
    fx_rate            numeric(10,4),
    fx_quoted_on       date,
    fx_source          text check (fx_source in ('ptax', 'fallback')),
    -- What the email was matched to when it was read (pending_cancellations
    -- matches by code, so a booking loaded later still finds it)
    target             text not null
                       check (target in ('booking', 'review_item', 'already_cancelled', 'not_found')),
    booking_id         bigint references pipeline.bookings,
    review_id          bigint references pipeline.booking_review_queue,
    created_at         timestamptz not null default now(),

    check ((outcome = 'penalty') = (penalty_amount is not null)),
    check ((penalty_amount is null) = (currency is null and penalty_brl is null)),
    -- A USD penalty carries the rate it was converted with; anything else none
    check (
        coalesce(currency = 'USD', false)
        = (fx_rate is not null and fx_quoted_on is not null and fx_source is not null)
    ),
    check ((target in ('booking', 'already_cancelled')) = (booking_id is not null)),
    check ((target = 'review_item') = (review_id is not null))
);

-- pending_cancellations looks events up by code: one can arrive before its booking
create index cancellation_events_code_idx
    on pipeline.cancellation_events (platform, confirmation_code);
