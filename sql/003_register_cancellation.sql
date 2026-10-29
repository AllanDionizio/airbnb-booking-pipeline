-- register_cancellation(payload jsonb) → jsonb
--
-- Records one parsed cancellation email and points it at what it cancels.
-- It never cancels anything: changing a booking's status (and replacing its
-- amount with a penalty) is a person's call, made from the
-- pending_cancellations view below.
--
-- Payload (built by load/postgres.py):
--   {"message_id": "...", "platform": "airbnb", "confirmation_code": "...",
--    "outcome": "full_refund" | "penalty" | "undetermined",
--    "penalty_amount": null | 315.4, "currency": null | "BRL" | "USD",
--    "fx": null | {"rate": 5.1234, "quoted_on": "YYYY-MM-DD", "source": "ptax"}}
--
-- Result codes:
--   INVALID_PAYLOAD      malformed input; nothing written
--   ALREADY_PROCESSED    the same email again; the earlier event is returned
--   FLAGGED              the booking (draft or confirmed) awaits confirmation
--   FLAGGED_REVIEW_ITEM  no booking, but an open review-queue item matches
--   ALREADY_CANCELLED    the booking was cancelled already; event recorded
--   NOT_FOUND            nothing matches the code yet; event recorded

create or replace function pipeline.register_cancellation(p jsonb)
returns jsonb
language plpgsql
set search_path = pipeline, pg_temp
as $$
declare
    v_message_id  text := nullif(btrim(p->>'message_id'), '');
    v_platform    text := nullif(btrim(p->>'platform'), '');
    v_code        text := nullif(btrim(p->>'confirmation_code'), '');
    v_outcome     text := p->>'outcome';
    v_currency    text := p->>'currency';
    v_fx          jsonb := nullif(p->'fx', 'null'::jsonb);
    v_penalty     numeric;
    v_penalty_brl numeric;
    v_valid       boolean := true;
    v_prior       cancellation_events%rowtype;
    v_booking     bookings%rowtype;
    v_review_id   bigint;
    v_target      text;
    v_result      text;
    v_event_id    bigint;
begin
    -- 1. Malformed payload: not recorded, so the email can be retried
    begin
        v_penalty := (p->>'penalty_amount')::numeric;
        v_penalty_brl := case v_currency
            when 'BRL' then v_penalty
            when 'USD' then round(v_penalty * (v_fx->>'rate')::numeric, 2)
        end;
    exception when others then
        v_valid := false;
    end;
    if not v_valid
       or v_message_id is null or v_platform is null or v_code is null
       or v_outcome is null or v_outcome not in ('full_refund', 'penalty', 'undetermined')
       -- a penalty, and only a penalty, has a positive amount and a currency
       or (v_outcome = 'penalty') <> (coalesce(v_penalty, 0) > 0 and v_currency is not null)
       or (v_outcome <> 'penalty' and (v_penalty is not null or v_currency is not null))
       or (v_currency is not null and v_currency not in ('BRL', 'USD'))
       or coalesce(v_currency = 'USD', false) <> (v_fx is not null)
    then
        return jsonb_build_object(
            'code', 'INVALID_PAYLOAD',
            'event_id', null,
            'booking_id', null,
            'review_id', null,
            'message', 'Payload is missing required fields or has inconsistent values.'
        );
    end if;

    -- 2. Idempotency by email
    select * into v_prior from cancellation_events where message_id = v_message_id;
    if found then
        return jsonb_build_object(
            'code', 'ALREADY_PROCESSED',
            'event_id', v_prior.event_id,
            'booking_id', v_prior.booking_id,
            'review_id', v_prior.review_id,
            'message', 'Cancellation email already recorded.'
        );
    end if;

    -- 3. Target: the booking, else the newest open review-queue item
    select * into v_booking
    from bookings
    where platform = v_platform and confirmation_code = v_code;

    if v_booking.booking_id is not null then
        if v_booking.status = 'cancelled' then
            v_target := 'already_cancelled';
            v_result := 'ALREADY_CANCELLED';
        else
            v_target := 'booking';
            v_result := 'FLAGGED';
        end if;
    else
        select review_id into v_review_id
        from booking_review_queue
        where platform = v_platform and confirmation_code = v_code and status = 'open'
        order by review_id desc
        limit 1;

        if v_review_id is not null then
            v_target := 'review_item';
            v_result := 'FLAGGED_REVIEW_ITEM';
        else
            v_target := 'not_found';
            v_result := 'NOT_FOUND';
        end if;
    end if;

    insert into cancellation_events (
        message_id, platform, confirmation_code, outcome,
        penalty_amount, currency, penalty_brl, fx_rate, fx_quoted_on, fx_source,
        target, booking_id, review_id
    )
    values (
        v_message_id, v_platform, v_code, v_outcome,
        v_penalty, v_currency, v_penalty_brl,
        (v_fx->>'rate')::numeric, (v_fx->>'quoted_on')::date, v_fx->>'source',
        v_target, v_booking.booking_id, v_review_id
    )
    returning event_id into v_event_id;

    return jsonb_build_object(
        'code', v_result,
        'event_id', v_event_id,
        'booking_id', v_booking.booking_id,
        'review_id', v_review_id,
        'message', case v_result
            when 'FLAGGED' then 'Booking flagged; a person confirms the cancellation.'
            when 'FLAGGED_REVIEW_ITEM' then 'Review-queue item flagged as cancelled at the source.'
            when 'ALREADY_CANCELLED' then 'Booking already cancelled; event recorded.'
            else 'No booking or review item matches; event recorded.'
        end
    );
end;
$$;


-- Live bookings with a cancellation email waiting for a person. The newest
-- event wins when Airbnb sends more than one. Events are matched by
-- confirmation code, not by the booking_id they were recorded with, so a
-- cancellation that arrived before its booking (NOT_FOUND, or flagged on a
-- review-queue item) shows up once the booking exists.
create or replace view pipeline.pending_cancellations as
select
    b.booking_id,
    b.confirmation_code,
    b.status,
    b.check_in,
    b.check_out,
    b.original_currency,
    e.outcome,
    e.penalty_brl,
    e.event_id,
    e.created_at as flagged_at
from pipeline.bookings as b
cross join lateral (
    select *
    from pipeline.cancellation_events as ce
    where ce.platform = b.platform
      and ce.confirmation_code = b.confirmation_code
    order by ce.event_id desc
    limit 1
) as e
where b.status <> 'cancelled';
